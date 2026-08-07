"""What user IDs are called, remembered from traffic Timothy already has.

Every page of the web UI is a list of snowflakes, and a snowflake names nobody. This is
the cache that lets those pages say who they are about, and it is deliberately the
weakest thing in the codebase: it is written opportunistically, it is read by exactly one
endpoint, and no decision anywhere is allowed to consult it. A name is a label on a row,
never a key — see :class:`~timothy_core.db.models.UserName`.

Two of the three writers are free: a login and a gateway event the bot relays both carry
a name Timothy would have received anyway. The third is the daily backfill (ADR 0017),
which asks Discord about the IDs the first two never reach — the thousands migrated in
from years of listings, whose owners may never join a guild Timothy is in or log in to
anything. Without it those stay bare numbers permanently, which is what
:func:`without_names` and the `backfill_user_names` job exist to fix.

Nothing here calls Discord itself. This module holds the queries; the job holds the
calls, so that every Discord call in the backend still goes through the worker
(ADR 0003) and inherits its pacing and its retries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import select, union

from timothy_core.db.models import (
    EnforcementOutcome,
    GuildException,
    Listing,
    UserName,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

MAX_NAME: Final = 64
"""The column's width. Discord's own limit is well under this; a name that somehow
exceeds it is truncated rather than allowed to fail an insert, because failing would take
a login or a relayed join down with it."""

MAX_LOOKUP: Final = 200
"""How many IDs one resolution may ask about. A page renders at most a couple of
hundred rows, and the cap keeps a hand-written query from asking for the whole table."""


def _now() -> datetime:
    return datetime.now(UTC)


async def record(session: AsyncSession, *, user_id: int, name: str) -> None:
    """Remember what this user is currently called.

    Does not commit: this is always a passenger on somebody else's transaction — a login,
    a relayed event — and it must not be able to commit that caller's work early.

    A blank name is dropped rather than stored, because storing it would replace a good
    name with nothing and the UI would go back to showing the ID.
    """
    cleaned = name.strip()[:MAX_NAME]
    if not cleaned:
        return

    known = await session.get(UserName, user_id)
    if known is None:
        session.add(UserName(user_id=user_id, name=cleaned, observed_at=_now()))
        return
    if known.name != cleaned:
        known.name = cleaned
        known.observed_at = _now()


async def record_missing(session: AsyncSession, *, user_id: int) -> None:
    """Record that Discord was asked about this ID and had nobody.

    The row is the point: without it the backfill would ask about the same deleted
    accounts every day for the life of the deployment. A NULL name is never shown — the
    UI draws the ID, exactly as it does for an ID nobody has looked at yet — so this is
    bookkeeping the reader never sees.

    Does not overwrite a name that is already known. A user Discord cannot find today is
    more likely to be a deleted account than a reason to forget what they were called.
    """
    known = await session.get(UserName, user_id)
    if known is None:
        session.add(UserName(user_id=user_id, name=None, observed_at=_now()))
        return
    if known.name is None:
        known.observed_at = _now()


async def resolve(session: AsyncSession, user_ids: Iterable[int]) -> list[UserName]:
    """The named rows for these IDs. IDs with no name are absent, not blank.

    The distinction matters to the caller: absent means "nothing to show", which the UI
    draws as the ID alone, and there is no name that would mean the same thing. A row
    recording that Discord had nobody is absent too — for a reader those are the same
    answer, and only the backfill cares which.
    """
    wanted = list(dict.fromkeys(user_ids))[:MAX_LOOKUP]
    if not wanted:
        return []
    rows = await session.scalars(
        select(UserName).where(UserName.user_id.in_(wanted), UserName.name.is_not(None))
    )
    return list(rows)


async def without_names(session: AsyncSession, *, limit: int) -> list[int]:
    """User IDs the UI will draw that nobody has ever looked up, oldest listing first.

    Only IDs that appear on a page: everyone listed on a pool, everyone a guild has
    excepted, and everyone with an enforcement outcome recorded against them. An ID that
    appears nowhere is not worth a Discord call, and audit-log targets are deliberately
    excluded — they are free-form strings, and mining IDs out of them would be guessing.

    Ordered by ID so successive batches march through the backlog in a stable order
    rather than re-picking the same rows. `limit` is what keeps one round bounded; the
    rest wait for tomorrow.
    """
    drawn = union(
        select(Listing.user_id.label("user_id")),
        select(GuildException.user_id.label("user_id")),
        select(EnforcementOutcome.user_id.label("user_id")),
    ).subquery()
    looked_at = select(UserName.user_id)
    return list(
        await session.scalars(
            select(drawn.c.user_id)
            .where(drawn.c.user_id.not_in(looked_at))
            .order_by(drawn.c.user_id)
            .limit(limit)
        )
    )
