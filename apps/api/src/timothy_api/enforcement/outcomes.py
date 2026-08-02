"""Reading and writing the record that makes a ban attributable.

`enforcement_outcomes` is durable state rather than a log (ADR 0005). One row per
(guild, user, pool), updated in place, and the composite key is doing three jobs at
once: it is the attribution that makes reverting safe, the dedupe key that keeps
warnings to one per user, and the marker that lets a sweep skip everyone it has already
settled.

Nothing here commits. The caller owns the transaction, for the same reason
:func:`timothy_api.audit.record` leaves it alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from timothy_core.db.models import EnforcementOutcome
from timothy_core.enums import OutcomeStatus

if TYPE_CHECKING:
    from collections.abc import Collection

    from sqlalchemy.ext.asyncio import AsyncSession


async def record(  # noqa: PLR0913 — three of these are the composite primary key
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    pool_id: int,
    status: OutcomeStatus,
    reason: str | None = None,
) -> EnforcementOutcome:
    """Write what happened, replacing whatever this key said before.

    Replacing rather than appending is deliberate: the row answers "where does this
    stand *now*", and the history of how it got there is the audit log's job.
    """
    outcome = await session.get(EnforcementOutcome, (guild_id, user_id, pool_id))
    if outcome is None:
        outcome = EnforcementOutcome(
            guild_id=guild_id,
            user_id=user_id,
            pool_id=pool_id,
            status=status,
            reason=reason,
        )
        session.add(outcome)
        return outcome

    outcome.status = status
    outcome.reason = reason
    outcome.attempted_at = datetime.now(UTC)
    return outcome


async def banned_pool_ids(
    session: AsyncSession, *, guild_id: int, user_id: int
) -> frozenset[int]:
    """Pools whose enforcement Timothy recorded as a ban here.

    Non-empty is the whole of `banned_by_timothy` in
    :func:`~timothy_core.enforcement.decisions.decide_revert`.
    """
    rows = await session.scalars(
        select(EnforcementOutcome.pool_id).where(
            EnforcementOutcome.guild_id == guild_id,
            EnforcementOutcome.user_id == user_id,
            EnforcementOutcome.status == OutcomeStatus.BANNED,
        )
    )
    return frozenset(rows)


async def banned_users_in(session: AsyncSession, *, guild_id: int, pool_id: int) -> list[int]:
    """Everyone Timothy banned in this guild on this pool's account."""
    rows = await session.scalars(
        select(EnforcementOutcome.user_id)
        .where(
            EnforcementOutcome.guild_id == guild_id,
            EnforcementOutcome.pool_id == pool_id,
            EnforcementOutcome.status == OutcomeStatus.BANNED,
        )
        .order_by(EnforcementOutcome.user_id)
    )
    return list(rows)


async def banned_pairs_for_pool(
    session: AsyncSession, pool_id: int, *, user_id: int | None = None
) -> list[tuple[int, int]]:
    """Every (guild, user) Timothy banned on this pool's account, anywhere.

    Reachable after the pool row itself is gone, which is why the table holds no foreign
    keys. `user_id` narrows it to the one user a removed listing was about.
    """
    statement = select(EnforcementOutcome.guild_id, EnforcementOutcome.user_id).where(
        EnforcementOutcome.pool_id == pool_id,
        EnforcementOutcome.status == OutcomeStatus.BANNED,
    )
    if user_id is not None:
        statement = statement.where(EnforcementOutcome.user_id == user_id)

    rows = await session.execute(
        statement.order_by(EnforcementOutcome.guild_id, EnforcementOutcome.user_id)
    )
    return [(guild_id, user_id) for guild_id, user_id in rows]


async def clear(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    pool_ids: Collection[int] | None = None,
    statuses: Collection[OutcomeStatus] | None = None,
) -> None:
    """Forget outcomes for one user in one guild.

    Used where the recorded answer has stopped being true rather than having been
    superseded: a ban that has since been lifted is not a `banned` outcome any more, and
    leaving the row would have a later revert try to unban a user who is already back.
    """
    statement = delete(EnforcementOutcome).where(
        EnforcementOutcome.guild_id == guild_id,
        EnforcementOutcome.user_id == user_id,
    )
    if pool_ids is not None:
        statement = statement.where(EnforcementOutcome.pool_id.in_(pool_ids))
    if statuses is not None:
        statement = statement.where(EnforcementOutcome.status.in_(statuses))
    await session.execute(statement)
