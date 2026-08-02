"""Fixtures for the migration. The dumps they build are made in `dumps.py`."""

from pathlib import Path

import dumps
import pytest

from timothy_migration import guilds, plan, records
from timothy_migration.dump import Dump


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def dump_root(tmp_path: Path) -> Path:
    """A small deployment with every interesting shape in it.

    Two pools; a user listed in both; a user listed only in the private one; a guild
    subscribed at `warn`; a guild that has configured nothing at all and rides the old
    implicit global; an exception; a notification channel.
    """
    return dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global", "the shared banlist"), dumps.pool("raiders")],
        bans=[
            dumps.ban(1001, "global", reason="ban evasion"),
            dumps.ban(1001, "raiders", reason="raid organiser"),
            dumps.ban(1002, "raiders", reason="raid participant"),
        ],
        subscriptions=[
            dumps.subscription(2001, "raiders", "ban"),
            dumps.subscription(2002, "raiders", "warn"),
        ],
        exceptions=[dumps.exception(2001, 1002)],
        notifications=[dumps.notification(2002, 3001)],
    )


@pytest.fixture
def snapshot_path(tmp_path: Path) -> Path:
    """Three guilds: the two that configured something, and one that never did."""
    return dumps.snapshot(tmp_path / "guilds.json", [2001, 2002, 2003])


@pytest.fixture
def source(dump_root: Path) -> records.Source:
    return records.read(Dump(dump_root))


@pytest.fixture
def snapshot(snapshot_path: Path) -> guilds.Snapshot:
    return guilds.Snapshot.read(snapshot_path)


@pytest.fixture
def import_plan(source: records.Source, snapshot: guilds.Snapshot) -> plan.ImportPlan:
    return plan.build(source, snapshot)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """Where an import writes. Deliberately does not exist yet."""
    return tmp_path / "timothy.db"
