"""Writing the plan into a SQLite file.

Deliberately dull. Every decision was already made in :mod:`timothy_migration.plan`;
this brings the schema to head and inserts, and its only real job is to refuse to write
into a database that already has something in it.

That refusal matters more than it looks. The import is not idempotent and cannot be: it
assigns surrogate keys from scratch, so running it twice over a live database would give
one set of listings pool IDs the other set does not share. There is no partial re-run —
there is a fresh file, or there is a restored backup and then a fresh file.

The insert goes through SQLAlchemy Core rather than the ORM. The rows are already exactly
the columns, the plan already resolved every relationship to an ID, and `insert().values`
over a list is one statement per table instead of one per row — which for a listings
table in the tens of thousands is the difference between a coffee and a commute.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, insert, select

from timothy_core.db.engine import make_engine
from timothy_core.db.models import (
    EnforcementOutcome,
    Guild,
    GuildException,
    Listing,
    NotificationChannel,
    Pool,
    Subscription,
)
from timothy_core.migrations import upgrade_to_head

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncConnection

    from timothy_migration.plan import ImportPlan, PlannedRow


class LoadError(Exception):
    """The database will not take this plan."""


def sqlite_url(path: Path) -> str:
    """The async URL for a SQLite file, in the form the backend's settings use."""
    return f"sqlite+aiosqlite:///{path}"


async def load(plan: ImportPlan, path: Path) -> None:
    """Create `path`, migrate it to head, and write every row in `plan`.

    Raises:
        LoadError: `path` already holds Timothy data. Import into a new file and move it
            into place; there is no meaningful merge.
    """
    if _already_there(path):
        await _refuse_if_populated(path)

    url = sqlite_url(path)
    await asyncio.to_thread(upgrade_to_head, url)

    engine = make_engine(url)
    try:
        async with engine.begin() as connection:
            await _write(connection, plan)
    finally:
        await engine.dispose()


def _already_there(path: Path) -> bool:
    """Whether `path` is a file with anything in it.

    Sync, and called from the async caller rather than inlined into it, because touching
    the filesystem from a coroutine blocks the loop — one `stat` does not matter here,
    but the rule is worth keeping unbroken.
    """
    return path.exists() and path.stat().st_size > 0


async def _refuse_if_populated(path: Path) -> None:
    """An existing file is fine if it is empty of Timothy's own rows, and not otherwise.

    A file that only has `alembic_version` in it is a database the backend has started
    against and never been used — the ordinary result of bringing the stack up once
    before the cutover — and importing into that is exactly right.
    """
    engine = make_engine(sqlite_url(path))
    try:
        async with engine.connect() as connection:
            occupied = [
                table.__tablename__
                for table in (Pool, Listing, Guild, Subscription)
                if await _has_rows(connection, table)
            ]
    except Exception as error:
        msg = f"{path} exists and could not be inspected: {error}"
        raise LoadError(msg) from error
    finally:
        await engine.dispose()

    if occupied:
        msg = (
            f"{path} already holds Timothy data ({', '.join(occupied)}). The import "
            f"assigns pool IDs from scratch and cannot be re-run over live data — "
            f"import into a new file, then move it into place."
        )
        raise LoadError(msg)


async def _has_rows(connection: AsyncConnection, table: type[Any]) -> bool:
    try:
        result = await connection.execute(select(func.count()).select_from(table))
    except Exception:  # noqa: BLE001 — the table not existing means no rows in it
        return False
    return bool(result.scalar_one())


async def _write(connection: AsyncConnection, plan: ImportPlan) -> None:
    """Insert every table, parents before children.

    Order is load-bearing: `foreign_keys` is ON for every connection Timothy opens
    (`timothy_core.db.engine`), so a subscription inserted before its guild fails rather
    than dangling. That the order has to be right is the point — it is the schema
    checking the plan.
    """
    await _insert(connection, Pool, plan.pools)
    await _insert(connection, Listing, plan.listings)
    await _insert(connection, Guild, plan.guilds)
    await _insert(connection, Subscription, plan.subscriptions)
    await _insert(connection, GuildException, plan.exceptions)
    await _insert(connection, NotificationChannel, plan.notification_channels)


async def _insert(
    connection: AsyncConnection, table: type[Any], rows: Sequence[PlannedRow]
) -> None:
    if not rows:
        return
    await connection.execute(insert(table), [_columns(row) for row in rows])


def _columns(row: PlannedRow) -> dict[str, Any]:
    """A planned row as its columns, one level deep.

    `dataclasses.asdict` would recurse, and `created_by` is an
    :class:`~timothy_core.actors.Actor` — itself a dataclass. Flattening it to
    `{"user_id": ...}` would hand SQLAlchemy a dict where its `ActorColumn` expects an
    actor, and the insert would fail on a type error three layers down from the cause.
    """
    return {field.name: getattr(row, field.name) for field in fields(row)}


async def count_enforcement_outcomes(path: Path) -> int:
    """How many enforcement outcomes the database holds.

    Read by the report to assert the one thing a fresh import must be able to say: it
    wrote none. An outcome is Timothy's claim to have issued a ban itself (ADR 0005), and
    the old bot's bans are not Timothy's — inventing outcomes for them would arm the
    revert path against every ban the old bot ever placed, and the first unsubscribe
    after cutover would lift thousands of them.
    """
    engine = make_engine(sqlite_url(path))
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                select(func.count()).select_from(EnforcementOutcome)
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()
