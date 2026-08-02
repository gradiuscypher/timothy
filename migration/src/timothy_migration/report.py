"""Saying what happened, in a form somebody will actually read.

Plain text to stdout. The audience is one person, once, on cutover morning, deciding
whether to keep going — so the shape is: what went in, what came out, what was decided on
your behalf, and what is still wrong. The last section is the one that matters and it is
last, because that is where a reader who scrolled to the bottom will look.

Long lists are truncated in the printed report and never in the JSON. An operator needs
to see that there are 4,102 orphaned listings and what three of them look like; the
machine-readable copy is where the other 4,099 live.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from timothy_migration.check import Comparison
    from timothy_migration.dump import Dump
    from timothy_migration.plan import ImportPlan
    from timothy_migration.records import Source

EXAMPLES: Final = 3
"""How many of a repeated finding to print before summarising the rest."""


def counted(number: int, noun: str) -> str:
    """`1 guild`, `3 guilds`. The report is prose; "1 guilds" reads as a bug in it."""
    return f"{number:,} {noun}" if number == 1 else f"{number:,} {noun}s"


def heading(text: str) -> str:
    """A section rule wide enough to find when scrolling."""
    return f"\n{text}\n{'─' * len(text)}"


def lines(text: str, values: Iterable[str], *, limit: int = EXAMPLES) -> list[str]:
    """A bulleted list, truncated with a count of what was left out."""
    items = list(values)
    shown = [f"  · {item}" for item in items[:limit]]
    if len(items) > limit:
        shown.append(f"  · … and {len(items) - limit:,} more")
    return [text, *shown] if items else []


def import_report(source_dump: Dump, source: Source, plan: ImportPlan) -> str:
    """What the import read, wrote and decided."""
    out: list[str] = [heading("Read from the dump")]
    out += [f"  {name:<24} {count:>8,}" for name, count in source.counts().items()]
    if source_dump.missing:
        out.append(f"  (absent from the dump: {', '.join(source_dump.missing)})")
    if source_dump.dead_present:
        out.append(
            f"  (present and deliberately not imported: {', '.join(source_dump.dead_present)})"
        )

    out.append(heading("Written to SQLite"))
    out += [f"  {name:<24} {count:>8,}" for name, count in plan.counts().items()]
    out.append(f"  {'enforcement_outcomes':<24} {0:>8,}  (see below)")

    if source.rejected:
        out.append(heading("Documents that could not be imported"))
        by_reason: dict[str, list[str]] = {}
        for rejection in source.rejected:
            by_reason.setdefault(f"{rejection.collection}: {rejection.reason}", []).append(
                str(rejection.document)
            )
        for reason, documents in sorted(by_reason.items()):
            out += lines(f"  {reason} ({len(documents):,})", documents, limit=1)

    grouped = plan.anomalies_by_kind()
    if grouped:
        out.append(heading("Decided during the import"))
        for kind, details in grouped.items():
            out += lines(f"  {kind.value} ({len(details):,})", details)

    out.append(heading("What was deliberately not written"))
    out.append(
        "  enforcement_outcomes is empty, and has to be. An outcome is Timothy's claim\n"
        "  to have issued a ban itself (ADR 0005), and every ban in these guilds today\n"
        "  was issued by the old bot. Inventing outcomes for them would arm the revert\n"
        "  path against thousands of bans Timothy never placed; the first unsubscribe\n"
        "  after cutover would lift them all. Timothy takes attribution for a ban when\n"
        "  it issues one, and not before."
    )
    return "\n".join(out) + "\n"


def comparison_report(title: str, comparison: Comparison, *, note: str = "") -> str:
    """A `verify` or `diff` result."""
    out: list[str] = [heading(title)]
    out.append(f"  {counted(comparison.pairs_compared, '(guild, user) pair')} compared")
    if note:
        out.append(f"  {note}")

    out.append("")
    for verdict, count in comparison.tally().items():
        out.append(f"  {verdict.value:<34} {count:>8,}")

    by_verdict: dict[str, list[str]] = {}
    for finding in comparison.findings:
        by_verdict.setdefault(finding.verdict.value, []).append(
            f"guild {finding.guild_id}, user {finding.user_id} — {finding.detail}"
        )
    for verdict, details in by_verdict.items():
        out += lines(f"\n  {verdict}:", details)

    out.append("")
    if comparison.unexplained:
        out.append(
            f"  NOT READY: {counted(len(comparison.unexplained), 'finding')} that no intended\n"
            f"  change accounts for. Every one of them is the import having invented a\n"
            f"  subscription, a listing, or a missing exception. Do not switch dry run\n"
            f"  off until this is zero."
        )
    else:
        out.append(
            "  No unexplained findings. Every difference above is a policy change with\n"
            "  an ADR behind it — read the counts and agree with them before continuing."
        )
    return "\n".join(out) + "\n"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """The same information, untruncated, for anything that is not a person."""
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def import_json(source: Source, plan: ImportPlan) -> dict[str, Any]:
    """The import report as data."""
    return {
        "read": source.counts(),
        "written": plan.counts(),
        "rejected": [
            {
                "collection": rejection.collection,
                "reason": rejection.reason,
                "document": rejection.document,
            }
            for rejection in source.rejected
        ],
        "anomalies": [
            {"kind": note.kind.value, "detail": note.detail} for note in plan.anomalies
        ],
    }


def comparison_json(comparison: Comparison) -> dict[str, Any]:
    """A comparison as data."""
    return {
        "pairs_compared": comparison.pairs_compared,
        "tally": {verdict.value: count for verdict, count in comparison.tally().items()},
        "findings": [
            {
                "verdict": finding.verdict.value,
                "guild_id": str(finding.guild_id),
                "user_id": str(finding.user_id),
                "detail": finding.detail,
            }
            for finding in comparison.findings
        ],
        "unexplained": len(comparison.unexplained),
    }
