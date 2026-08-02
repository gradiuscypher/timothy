"""The dynamic check: what a dry run intended, against what the old bot would have done.

The rehearsal database is built here by writing the audit rows the enforcement engine
writes — `enforcement.dry_run`, target `guild:<id>/user:<id>`, detail `{"would": ...}`.
`apps/api/tests/test_dry_run.py` is what proves the engine really writes them in that
shape; this proves the check reads that shape.
"""

from pathlib import Path
from typing import Any

import dumps
import pytest

from timothy_core.actors import Actor
from timothy_core.db.engine import make_engine, make_sessionmaker
from timothy_core.db.models import AuditLogEntry
from timothy_migration import check, guilds, load, plan, records
from timothy_migration.dump import Dump
from timothy_migration.oldbot import OldBot


async def rehearse(
    database: Path, import_plan: plan.ImportPlan, intentions: list[tuple[int, int, str]]
) -> None:
    """Import, then write the audit rows a dry run would have left behind."""
    await load.load(import_plan, database)

    engine = make_engine(load.sqlite_url(database))
    try:
        async with make_sessionmaker(engine)() as session:
            for guild_id, user_id, would in intentions:
                session.add(
                    AuditLogEntry(
                        actor=Actor.system(),
                        action=check.DRY_RUN_ACTION,
                        target=f"guild:{guild_id}/user:{user_id}",
                        detail={"would": would, "reason": "listed"},
                    )
                )
            # Something the check must not mistake for an intention.
            session.add(
                AuditLogEntry(
                    actor=Actor.user(7),
                    action="pool.create",
                    target="pool:raiders",
                    detail=None,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_it_reads_only_dry_run_rows(import_plan: plan.ImportPlan, database: Path) -> None:
    await rehearse(database, import_plan, [(2001, 1001, "ban")])

    intentions = await check.read_intentions(database)

    assert intentions == [check.Intention(guild_id=2001, user_id=1001, would="ban")]


@pytest.mark.anyio
async def test_an_intended_ban_the_old_bot_agrees_with(
    import_plan: plan.ImportPlan, source: records.Source, database: Path
) -> None:
    await rehearse(database, import_plan, [(2001, 1001, "ban")])

    comparison = check.diff(await check.read_intentions(database), OldBot.from_source(source))

    assert comparison.findings == []
    assert comparison.tally() == {check.Verdict.AGREES: 1}


@pytest.mark.anyio
async def test_an_intended_warn_is_an_expected_difference(
    import_plan: plan.ImportPlan, source: records.Source, database: Path
) -> None:
    """Guild 2002 holds `raiders` at warn. The old bot's live check ignored that and
    banned; Timothy warns."""
    await rehearse(database, import_plan, [(2002, 1002, "warn")])

    comparison = check.diff(await check.read_intentions(database), OldBot.from_source(source))

    assert [finding.verdict for finding in comparison.findings] == [check.Verdict.NOW_WARNS]
    assert comparison.unexplained == []


@pytest.mark.anyio
async def test_an_intention_the_old_bot_would_not_have_had_is_unexplained(
    import_plan: plan.ImportPlan, source: records.Source, database: Path
) -> None:
    """User 1002 is excepted in guild 2001. A dry run intending to ban them there means
    the exception did not survive."""
    await rehearse(database, import_plan, [(2001, 1002, "ban")])

    comparison = check.diff(await check.read_intentions(database), OldBot.from_source(source))

    assert [finding.verdict for finding in comparison.unexplained] == [
        check.Verdict.NEWLY_ENFORCED
    ]


@pytest.mark.anyio
async def test_repeated_intentions_are_counted_not_collapsed(
    import_plan: plan.ImportPlan, source: records.Source, database: Path
) -> None:
    """Dry run does not dedupe (ADR 0009) — with no outcome row written there is nothing
    to dedupe against, so the same pair three times means three sweeps."""
    await rehearse(database, import_plan, [(2001, 1001, "ban")] * 3)

    comparison = check.diff(await check.read_intentions(database), OldBot.from_source(source))

    assert comparison.pairs_compared == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target", "detail"),
    [
        ("guild:2001", {"would": "ban"}),
        ("guild:abc/user:1001", {"would": "ban"}),
        ("guild:2001/user:1001", None),
        ("guild:2001/user:1001", {"blocked": "no notification channel"}),
        (None, {"would": "ban"}),
    ],
)
async def test_a_dry_run_row_of_an_unfamiliar_shape_is_skipped(
    import_plan: plan.ImportPlan,
    database: Path,
    target: str | None,
    detail: dict[str, Any] | None,
) -> None:
    """The audit log is append-only and shared. A check that fell over on an unfamiliar
    row would stop working the first time anything else was added to it."""
    await load.load(import_plan, database)
    engine = make_engine(load.sqlite_url(database))
    try:
        async with make_sessionmaker(engine)() as session:
            session.add(
                AuditLogEntry(
                    actor=Actor.system(),
                    action=check.DRY_RUN_ACTION,
                    target=target,
                    detail=detail,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()

    assert await check.read_intentions(database) == []


@pytest.mark.anyio
async def test_a_database_with_no_rehearsal_yields_nothing(
    import_plan: plan.ImportPlan, database: Path
) -> None:
    """Which the CLI treats as a failure — either the rehearsal has not run, or dry run
    was off and it enforced for real."""
    await load.load(import_plan, database)

    assert await check.read_intentions(database) == []


@pytest.mark.anyio
async def test_the_diff_cannot_see_silence(tmp_path: Path, database: Path) -> None:
    """The honest limit of this check, stated as a test. A user the old bot would ban and
    Timothy skips writes no audit row at all, so the diff reports nothing — which is the
    gap `verify` exists to close."""
    source = records.read(
        Dump(
            dumps.build(
                tmp_path / "dump",
                banpools=[dumps.pool("global")],
                bans=[dumps.ban(1001, "global")],
            )
        )
    )
    import_plan = plan.build(
        source,
        guilds.Snapshot.read(dumps.snapshot(tmp_path / "guilds.json", [2001])),
    )
    import_plan.listings.clear()
    await rehearse(database, import_plan, [])

    comparison = check.diff(await check.read_intentions(database), OldBot.from_source(source))

    assert comparison.findings == []
    assert comparison.pairs_compared == 0
