"""Writing the plan into SQLite, and refusing to write it twice."""

import sqlite3
from pathlib import Path

import dumps
import pytest
from sqlalchemy import select

from timothy_core.actors import Actor
from timothy_core.db.engine import make_engine
from timothy_core.db.models import Listing, Pool, Subscription
from timothy_core.migrations import upgrade_to_head
from timothy_migration import guilds, load, plan, records
from timothy_migration.dump import Dump


def tables(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()


@pytest.mark.anyio
async def test_it_migrates_and_writes(import_plan: plan.ImportPlan, database: Path) -> None:
    await load.load(import_plan, database)

    assert {"pools", "listings", "subscriptions", "alembic_version"} <= tables(database)


@pytest.mark.anyio
async def test_the_rows_come_back_as_they_went_in(
    import_plan: plan.ImportPlan, database: Path
) -> None:
    await load.load(import_plan, database)

    engine = make_engine(load.sqlite_url(database))
    try:
        async with engine.connect() as connection:
            pools = (await connection.execute(select(Pool.id, Pool.name))).all()
            listings = (await connection.execute(select(Listing.user_id))).all()
            subscriptions = (await connection.execute(select(Subscription.guild_id))).all()
    finally:
        await engine.dispose()

    assert sorted(pools) == [(1, "global"), (2, "raiders")]
    assert len(listings) == 3
    assert len(subscriptions) == len(import_plan.subscriptions)


@pytest.mark.anyio
async def test_the_actor_survives_the_round_trip(
    import_plan: plan.ImportPlan, database: Path
) -> None:
    """`asdict` would have flattened `Actor` into `{"user_id": ...}` on the way in."""
    await load.load(import_plan, database)

    engine = make_engine(load.sqlite_url(database))
    try:
        async with engine.connect() as connection:
            actors = (await connection.execute(select(Pool.created_by))).scalars().all()
    finally:
        await engine.dispose()

    assert set(actors) == {Actor.system()}


@pytest.mark.anyio
async def test_it_writes_no_enforcement_outcomes(
    import_plan: plan.ImportPlan, database: Path
) -> None:
    """An outcome is Timothy's claim to have issued a ban itself (ADR 0005). Every ban in
    these guilds today was the old bot's, and inventing outcomes for them would have the
    first unsubscribe after cutover lift thousands of bans Timothy never placed."""
    await load.load(import_plan, database)

    assert await load.count_enforcement_outcomes(database) == 0


@pytest.mark.anyio
async def test_it_refuses_a_database_that_already_holds_data(
    import_plan: plan.ImportPlan, database: Path
) -> None:
    """The import assigns pool IDs from scratch, so there is no meaningful re-run."""
    await load.load(import_plan, database)

    with pytest.raises(load.LoadError, match="already holds Timothy data"):
        await load.load(import_plan, database)


@pytest.mark.anyio
async def test_it_accepts_a_migrated_but_empty_database(
    import_plan: plan.ImportPlan, database: Path
) -> None:
    """The ordinary result of bringing the stack up once before the cutover."""
    upgrade_to_head(load.sqlite_url(database))

    await load.load(import_plan, database)

    assert await load.count_enforcement_outcomes(database) == 0


@pytest.mark.anyio
async def test_it_refuses_a_file_that_is_not_a_database(
    import_plan: plan.ImportPlan, database: Path
) -> None:
    database.write_bytes(b"not a database")  # noqa: ASYNC240 — arrangement, before the first await

    with pytest.raises(load.LoadError):
        await load.load(import_plan, database)


@pytest.mark.anyio
async def test_foreign_keys_are_live_during_the_load(tmp_path: Path, database: Path) -> None:
    """A subscription for a guild with no row fails rather than dangling — the schema
    checking the plan, which is why the insert order is written down."""
    source = records.read(
        Dump(
            dumps.build(
                tmp_path / "dump",
                banpools=[dumps.pool("global")],
                subscriptions=[dumps.subscription(2001, "global")],
            )
        )
    )
    snapshot = guilds.Snapshot.read(dumps.snapshot(tmp_path / "guilds.json", [2001]))
    broken = plan.build(source, snapshot)
    broken.guilds.clear()

    with pytest.raises(Exception, match="FOREIGN KEY"):
        await load.load(broken, database)


@pytest.mark.anyio
async def test_it_accepts_a_database_file_with_no_schema(
    import_plan: plan.ImportPlan, database: Path
) -> None:
    """A SQLite file with nothing in it is not data — it is what `sqlite3 timothy.db`
    leaves behind when somebody looked."""
    sqlite3.connect(database).close()
    database.write_bytes(b"")  # noqa: ASYNC240 — arrangement, before the first await

    await load.load(import_plan, database)

    assert "pools" in tables(database)


@pytest.mark.anyio
async def test_a_database_holding_only_unrelated_tables_is_accepted(
    import_plan: plan.ImportPlan, database: Path
) -> None:
    """Counting rows in a table that does not exist is not an error, it is no rows."""
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    await load.load(import_plan, database)

    assert {"pools", "notes"} <= tables(database)
