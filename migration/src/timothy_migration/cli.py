"""`timothy-migrate` — four subcommands, in the order they are run.

The split is the safety property. `guilds fetch` is the only step that touches the
network and it writes a file; `import` reads files and writes a file; `verify` and `diff`
read files and write nothing. So the risky step is one command with one output, the
checks can be re-run as many times as it takes, and nothing in the sequence has to be
undone if the answer is "not yet".

Exit codes are meant for a person watching, not for a pipeline: `0` when the thing asked
for succeeded, `1` when it did not, and `2` when a check ran fine and its answer was that
the cutover is not ready. A shell that treats non-zero as failure gets a defensible
answer; one that distinguishes them gets a better one.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

from timothy_migration import check, dump, guilds, load, plan, records, report
from timothy_migration.oldbot import OldBot

if TYPE_CHECKING:
    from collections.abc import Sequence

TOKEN_VAR: Final = "TIMOTHY_DISCORD_TOKEN"  # noqa: S105 — the variable's name, not a token
"""The same variable the backend and the bot read, so the cutover uses one credential
and nobody has to be told to export a second."""

OK: Final = 0
FAILED: Final = 1
NOT_READY: Final = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand.

    Returns the process exit code rather than raising, so the tests can call it.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (
        dump.DumpError,
        guilds.GuildFetchError,
        plan.PlanError,
        load.LoadError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return FAILED


def _run(args: argparse.Namespace) -> int:
    match args.command:
        case "guilds":
            return _fetch_guilds(args)
        case "import":
            return _import(args)
        case "verify":
            return _verify(args)
        case "diff":
            return _diff(args)
        case _:  # pragma: no cover — argparse rejects anything else first
            msg = f"unknown command {args.command!r}"
            raise AssertionError(msg)


# -- guilds fetch ------------------------------------------------------------


def _fetch_guilds(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get(TOKEN_VAR, "")
    if not token:
        print(f"error: no bot token. Set {TOKEN_VAR} or pass --token.", file=sys.stderr)
        return FAILED

    snapshot = guilds.fetch(token)
    snapshot.write(args.output)
    print(f"{len(snapshot.guilds):,} guilds written to {args.output}")
    return OK


# -- import ------------------------------------------------------------------


def _import(args: argparse.Namespace) -> int:
    source_dump = dump.Dump(args.dump)
    source = records.read(source_dump)
    snapshot = guilds.Snapshot.read(args.guilds)
    import_plan = plan.build(source, snapshot, global_pool=args.global_pool)

    print(report.import_report(source_dump, source, import_plan))
    if args.dry_run:
        print("Nothing written: --dry-run. Re-run without it to write the database.")
        return OK

    asyncio.run(load.load(import_plan, args.database))
    outcomes = asyncio.run(load.count_enforcement_outcomes(args.database))
    if outcomes:  # pragma: no cover — only reachable if the loader is changed to write them
        print(
            f"error: {outcomes} enforcement outcomes were written; there must be none",
            file=sys.stderr,
        )
        return FAILED

    if args.report:
        report.write_json(args.report, report.import_json(source, import_plan))
    print(f"Written to {args.database}")
    return OK


# -- verify ------------------------------------------------------------------


def _verify(args: argparse.Namespace) -> int:
    source = records.read(dump.Dump(args.dump))
    imported = asyncio.run(check.read_imported(args.database))
    comparison = check.verify(imported, OldBot.from_source(source))

    print(
        report.comparison_report(
            "verify — every decision, both systems",
            comparison,
            note="every guild in the database against every user listed on either side",
        )
    )
    if args.report:
        report.write_json(args.report, report.comparison_json(comparison))
    return NOT_READY if comparison.unexplained else OK


# -- diff --------------------------------------------------------------------


def _diff(args: argparse.Namespace) -> int:
    source = records.read(dump.Dump(args.dump))
    intentions = asyncio.run(check.read_intentions(args.database))
    comparison = check.diff(intentions, OldBot.from_source(source))

    if not intentions:
        print(
            "No dry-run intentions in the audit log. Either the rehearsal has not run\n"
            "yet, or TIMOTHY_DRY_RUN was off and it enforced for real — check\n"
            "enforcement_outcomes before assuming the former.",
            file=sys.stderr,
        )
        return FAILED

    print(
        report.comparison_report(
            "diff — what the dry run intended",
            comparison,
            note=(
                "one row per intention, so a pair seen twice is two sweeps; silence is "
                "invisible here, which is what verify is for"
            ),
        )
    )
    if args.report:
        report.write_json(args.report, report.comparison_json(comparison))
    return NOT_READY if comparison.unexplained else OK


# -- argument parsing --------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="timothy-migrate",
        description="Mongo → SQLite import, and the checks that make the cutover reviewable.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    fetch = subcommands.add_parser(
        "guilds", help="fetch the guild list from Discord and write a snapshot"
    )
    fetch.add_argument(
        "--output",
        type=Path,
        default=Path("guilds.json"),
        help="where to write the snapshot (default: guilds.json)",
    )
    fetch.add_argument(
        "--token",
        default="",
        help=f"bot token; defaults to ${TOKEN_VAR}",
    )

    importer = subcommands.add_parser(
        "import", help="read a mongodump and a snapshot, write a SQLite database"
    )
    _add_dump(importer)
    importer.add_argument(
        "--guilds",
        type=Path,
        default=Path("guilds.json"),
        help="the snapshot from `timothy-migrate guilds` (default: guilds.json)",
    )
    _add_database(importer, "the SQLite file to create")
    importer.add_argument(
        "--global-pool",
        default="global",
        help=(
            "the pool every guild is subscribed to on cutover (ADR 0002). Must match "
            "the backend's TIMOTHY_AUTO_SUBSCRIBE_POOL. Empty disables materialisation."
        ),
    )
    importer.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written and write nothing",
    )
    _add_report(importer)

    verifier = subcommands.add_parser(
        "verify", help="compare every decision in the imported database against the dump"
    )
    _add_dump(verifier)
    _add_database(verifier, "the imported SQLite file")
    _add_report(verifier)

    differ = subcommands.add_parser(
        "diff", help="compare a dry run's audit log against the old bot's behaviour"
    )
    _add_dump(differ)
    _add_database(differ, "the SQLite file the rehearsal ran against")
    _add_report(differ)

    return parser


def _add_dump(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dump", type=Path, required=True, help="the mongodump directory")


def _add_database(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument("--database", type=Path, required=True, help=help_text)


def _add_report(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="also write the full, untruncated report here as JSON",
    )
