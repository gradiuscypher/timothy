"""The document an operator reads on cutover morning."""

import json
from pathlib import Path

import dumps
import pytest

from timothy_migration import check, guilds, plan, records, report
from timothy_migration.dump import Dump


@pytest.fixture
def messy(tmp_path: Path) -> tuple[Dump, records.Source, plan.ImportPlan]:
    """A dump with one of everything the report has a section for."""
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global"), dumps.pool("global", "again", days=3)],
        bans=[
            dumps.ban(1001, "global"),
            dumps.ban(1002, "gone-in-2021"),
            {"user_id": "not-a-number", "pool_name": "global"},
        ],
        exceptions=[dumps.exception(9999, 1001)],
    )
    dumps.write_collection(root, "serverconfig", [{"server_id": "1"}])

    source_dump = Dump(root)
    source = records.read(source_dump)
    snapshot = guilds.Snapshot.read(dumps.snapshot(tmp_path / "guilds.json", [2001]))
    return source_dump, source, plan.build(source, snapshot)


def test_the_import_report_covers_every_section(
    messy: tuple[Dump, records.Source, plan.ImportPlan],
) -> None:
    source_dump, source, import_plan = messy

    printed = report.import_report(source_dump, source, import_plan)

    assert "Read from the dump" in printed
    assert "absent from the dump: subscriptions, notifications" in printed
    assert "present and deliberately not imported: serverconfig" in printed
    assert "Documents that could not be imported" in printed
    assert "not a Discord ID" in printed
    assert "Decided during the import" in printed
    assert "duplicate pool" in printed
    assert "listing in a pool that no longer exists" in printed
    assert "row for a guild Timothy is no longer in" in printed
    assert "enforcement_outcomes is empty" in printed


def test_long_lists_are_truncated_with_a_count() -> None:
    """An operator needs to see that there are 4,102 of something and what three of them
    look like; the JSON is where the other 4,099 live."""
    printed = report.lines("things:", [str(n) for n in range(10)])

    assert printed == ["things:", "  · 0", "  · 1", "  · 2", "  · … and 7 more"]


def test_an_empty_list_prints_nothing() -> None:
    assert report.lines("things:", []) == []


def test_the_json_report_is_not_truncated(
    messy: tuple[Dump, records.Source, plan.ImportPlan], tmp_path: Path
) -> None:
    _, source, import_plan = messy
    path = tmp_path / "import.json"

    report.write_json(path, report.import_json(source, import_plan))
    payload = json.loads(path.read_text())

    assert payload["read"]["bans"] == 2
    assert payload["written"]["pools"] == 1
    assert len(payload["rejected"]) == 1
    assert payload["rejected"][0]["collection"] == "bans"
    assert {note["kind"] for note in payload["anomalies"]} >= {
        "duplicate pool",
        "listing in a pool that no longer exists",
    }


def test_a_comparison_with_no_findings_says_so() -> None:
    printed = report.comparison_report("verify", check.Comparison(pairs_compared=7))

    assert "7 (guild, user) pairs compared" in printed
    assert "NOT READY" not in printed
    assert "No unexplained findings" in printed
    assert "NOT READY" not in printed


def test_a_comparison_with_an_unexplained_finding_says_not_ready() -> None:
    comparison = check.Comparison(
        pairs_compared=2,
        findings=[
            check.Finding(
                verdict=check.Verdict.NEWLY_ENFORCED,
                guild_id=2001,
                user_id=1001,
                detail="invented",
            )
        ],
    )

    printed = report.comparison_report("verify", comparison, note="a note")

    assert "a note" in printed
    assert "newly enforced" in printed
    assert "guild 2001, user 1001 — invented" in printed
    assert "NOT READY: 1 finding that no intended" in printed


def test_the_comparison_json_carries_ids_as_strings() -> None:
    """A snowflake past 2^53 loses precision as a JSON number."""
    comparison = check.Comparison(
        pairs_compared=1,
        findings=[
            check.Finding(
                verdict=check.Verdict.NOW_WARNS,
                guild_id=1234567890123456789,
                user_id=1,
                detail="warns",
            )
        ],
    )

    payload = report.comparison_json(comparison)

    assert payload["findings"][0]["guild_id"] == "1234567890123456789"
    assert payload["tally"] == {"now warns instead of banning": 1}
    assert payload["unexplained"] == 0


def test_the_heading_rule_matches_the_heading() -> None:
    assert report.heading("abc").splitlines()[1:] == ["abc", "───"]


def test_a_clean_import_has_no_decisions_section(tmp_path: Path) -> None:
    """The report only grows a section when there is something in it — an import that
    decided nothing on the operator's behalf should not print a heading over an empty
    list and invite them to look for what it was."""
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        bans=[dumps.ban(1001, "global")],
        subscriptions=[dumps.subscription(2001, "global")],
        exceptions=[],
        notifications=[],
    )
    source_dump = Dump(root)
    source = records.read(source_dump)
    snapshot = guilds.Snapshot.read(dumps.snapshot(tmp_path / "guilds.json", [2001]))

    printed = report.import_report(
        source_dump, source, plan.build(source, snapshot, global_pool="")
    )

    assert "Decided during the import" not in printed
    assert "Documents that could not be imported" not in printed


def test_the_report_counts_in_prose() -> None:
    """It is a document, and "1 guilds" reads as a bug in the document."""
    assert report.counted(1, "guild") == "1 guild"
    assert report.counted(4102, "guild") == "4,102 guilds"
