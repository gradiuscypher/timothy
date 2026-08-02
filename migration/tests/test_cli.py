"""The commands an operator actually types, end to end.

Every test here goes through `main()` with an argument list, because the runbook in
`docs/cutover.md` is a list of command lines and the thing that has to work is those.
"""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import dumps
import pytest

from timothy_core.actors import Actor
from timothy_core.db.engine import make_engine, make_sessionmaker
from timothy_core.db.models import AuditLogEntry
from timothy_migration import __main__, check, cli, guilds, load


def run(*argv: str) -> int:
    return cli.main(list(argv))


@pytest.fixture
def imported(dump_root: Path, snapshot_path: Path, database: Path) -> Path:
    """A database that has been through `timothy-migrate import`."""
    assert (
        run(
            "import",
            "--dump",
            str(dump_root),
            "--guilds",
            str(snapshot_path),
            "--database",
            str(database),
        )
        == cli.OK
    )
    return database


# -- import ------------------------------------------------------------------


def test_import_writes_the_database(imported: Path) -> None:
    assert imported.exists()


def test_import_reports_what_it_did(
    dump_root: Path, snapshot_path: Path, database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "import",
        "--dump",
        str(dump_root),
        "--guilds",
        str(snapshot_path),
        "--database",
        str(database),
    )
    printed = capsys.readouterr().out

    assert "Read from the dump" in printed
    assert "Written to SQLite" in printed
    assert "global subscription materialised" in printed
    assert "enforcement_outcomes is empty" in printed


def test_import_dry_run_writes_nothing(
    dump_root: Path, snapshot_path: Path, database: Path
) -> None:
    assert (
        run(
            "import",
            "--dump",
            str(dump_root),
            "--guilds",
            str(snapshot_path),
            "--database",
            str(database),
            "--dry-run",
        )
        == cli.OK
    )
    assert not database.exists()


def test_import_writes_a_json_report(
    dump_root: Path, snapshot_path: Path, database: Path, tmp_path: Path
) -> None:
    report = tmp_path / "import.json"
    run(
        "import",
        "--dump",
        str(dump_root),
        "--guilds",
        str(snapshot_path),
        "--database",
        str(database),
        "--report",
        str(report),
    )

    assert '"pools": 2' in report.read_text()


def test_import_refuses_a_dump_with_no_global_pool(
    tmp_path: Path, snapshot_path: Path, database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = dumps.build(tmp_path / "other", banpools=[dumps.pool("raiders")])

    code = run(
        "import",
        "--dump",
        str(root),
        "--guilds",
        str(snapshot_path),
        "--database",
        str(database),
    )

    assert code == cli.FAILED
    assert "no pool named 'global'" in capsys.readouterr().err


def test_import_refuses_a_database_that_already_holds_data(
    imported: Path, dump_root: Path, snapshot_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(
        "import",
        "--dump",
        str(dump_root),
        "--guilds",
        str(snapshot_path),
        "--database",
        str(imported),
    )

    assert code == cli.FAILED
    assert "already holds Timothy data" in capsys.readouterr().err


def test_import_refuses_a_directory_that_is_not_a_dump(
    tmp_path: Path, snapshot_path: Path, database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "empty").mkdir()

    code = run(
        "import",
        "--dump",
        str(tmp_path / "empty"),
        "--guilds",
        str(snapshot_path),
        "--database",
        str(database),
    )

    assert code == cli.FAILED
    assert "no collections found" in capsys.readouterr().err


# -- verify ------------------------------------------------------------------


def test_verify_passes_a_faithful_import(
    imported: Path, dump_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run("verify", "--dump", str(dump_root), "--database", str(imported))
    printed = capsys.readouterr().out

    assert code == cli.OK
    assert "No unexplained findings" in printed
    assert "now warns instead of banning" in printed


def test_verify_reports_not_ready_when_the_import_invented_something(
    imported: Path, dump_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit code 2: the check ran fine and its answer is that the cutover is not ready."""
    _drop_the_exceptions(imported)

    code = run("verify", "--dump", str(dump_root), "--database", str(imported))

    assert code == cli.NOT_READY
    assert "NOT READY" in capsys.readouterr().out


def _drop_the_exceptions(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM exceptions")
        connection.commit()
    finally:
        connection.close()


# -- diff --------------------------------------------------------------------


def test_diff_reads_a_rehearsal(
    imported: Path, dump_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _record_intention(imported, guild_id=2001, user_id=1001, would="ban")

    code = run("diff", "--dump", str(dump_root), "--database", str(imported))
    printed = capsys.readouterr().out

    assert code == cli.OK
    assert "1 (guild, user) pair compared" in printed


def test_diff_reports_not_ready_on_an_intention_the_old_bot_lacked(
    imported: Path, dump_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """User 1002 is excepted in guild 2001, so an intended ban there means the exception
    did not survive."""
    _record_intention(imported, guild_id=2001, user_id=1002, would="ban")

    code = run("diff", "--dump", str(dump_root), "--database", str(imported))

    assert code == cli.NOT_READY
    assert "NOT READY" in capsys.readouterr().out


def test_diff_refuses_a_database_with_no_rehearsal_in_it(
    imported: Path, dump_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Either the rehearsal has not run, or dry run was off and it enforced for real."""
    code = run("diff", "--dump", str(dump_root), "--database", str(imported))

    assert code == cli.FAILED
    assert "No dry-run intentions" in capsys.readouterr().err


def _record_intention(database: Path, *, guild_id: int, user_id: int, would: str) -> None:
    """Write the audit row a dry run would have left, as the engine writes it.

    Synchronous, because every test here goes through `main()` and `main()` calls
    `asyncio.run` — a test that was itself async would have it refuse to start a loop
    inside a loop.
    """

    async def record() -> None:
        engine = make_engine(load.sqlite_url(database))
        try:
            async with make_sessionmaker(engine)() as session:
                session.add(
                    AuditLogEntry(
                        actor=Actor.system(),
                        action=check.DRY_RUN_ACTION,
                        target=f"guild:{guild_id}/user:{user_id}",
                        detail={"would": would},
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(record())


# -- guilds fetch ------------------------------------------------------------


def test_guilds_fetch_needs_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(cli.TOKEN_VAR, raising=False)

    code = run("guilds", "--output", str(tmp_path / "guilds.json"))

    assert code == cli.FAILED
    assert cli.TOKEN_VAR in capsys.readouterr().err


def test_guilds_fetch_writes_a_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(cli.TOKEN_VAR, "a-token")
    monkeypatch.setattr(cli.guilds, "fetch", _snapshot_for)
    output = tmp_path / "guilds.json"

    assert run("guilds", "--output", str(output)) == cli.OK
    assert "2 guilds written" in capsys.readouterr().out
    assert guilds.Snapshot.read(output).guild_ids == {2001, 2002}


def _snapshot_for(token: str) -> guilds.Snapshot:
    assert token == "a-token"
    return guilds.Snapshot(
        fetched_at=datetime(2026, 8, 2, tzinfo=UTC),
        guilds=(
            guilds.GuildRecord(guild_id=2001, name="one"),
            guilds.GuildRecord(guild_id=2002, name="two"),
        ),
    )


def test_a_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        run()


def test_verify_writes_a_json_report(imported: Path, dump_root: Path, tmp_path: Path) -> None:
    report = tmp_path / "verify.json"

    run(
        "verify",
        "--dump",
        str(dump_root),
        "--database",
        str(imported),
        "--report",
        str(report),
    )

    assert '"unexplained": 0' in report.read_text()


def test_diff_writes_a_json_report(imported: Path, dump_root: Path, tmp_path: Path) -> None:
    _record_intention(imported, guild_id=2001, user_id=1001, would="ban")
    report = tmp_path / "diff.json"

    run(
        "diff",
        "--dump",
        str(dump_root),
        "--database",
        str(imported),
        "--report",
        str(report),
    )

    assert '"pairs_compared": 1' in report.read_text()


def test_the_module_is_runnable() -> None:
    """`python -m timothy_migration`, for a checkout without the console script on PATH."""
    assert __main__.main is cli.main
