"""Assembling the question `decide()` answers, and finding who to ask it about.

Two kinds of thing live here. Gathering builds one
:class:`~timothy_core.enforcement.decisions.EnforcementRequest` for one (guild, user).
Targeting turns a job's thin payload — a listing ID, a pool ID — into the set of pairs
that payload implies *now*, which is the reason the payloads stay thin: which guilds a
listing reaches is a question about subscriptions at the moment the worker runs.

The expensive fact is presence. `fetch_member` is a Discord call per user per guild, so
it is made only when the answer depends on it — `decide()` checks paused and not-listed
first, and the overwhelmingly common answer is not-listed, which costs no network at all.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from sqlalchemy import distinct, or_, select

from timothy_core.db.models import (
    EnforcementOutcome,
    Guild,
    GuildException,
    Listing,
    Pool,
    Subscription,
)
from timothy_core.enforcement.decisions import (
    EnforcementRequest,
    GuildEnforcementState,
    PoolListing,
    subscribed_listings,
)
from timothy_core.enums import OutcomeStatus, SubscriptionLevel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from timothy_core.ports.discord import DiscordPort

SETTLED: frozenset[OutcomeStatus] = frozenset(
    {OutcomeStatus.BANNED, OutcomeStatus.WARNED, OutcomeStatus.SKIPPED_EXCEPTION}
)
"""Outcomes that mean this (guild, user, pool) needs no further attention.

`FAILED` is deliberately absent: a failure is the case the sweep exists to pick up, and
ADR 0004's "retroactive ban failure correction" is exactly this set not containing it.
"""


# -- gathering ---------------------------------------------------------------


async def guild_state(session: AsyncSession, guild_id: int) -> GuildEnforcementState | None:
    """What the guild has asked for, or `None` if Timothy has no record of being in it.

    A guild Timothy has left is not an error here. Jobs outlive the thing that queued
    them, and deregistration cascades a guild's subscriptions away while its enforcement
    outcomes survive, so an in-flight job finding nothing is ordinary.
    """
    guild = await session.get(Guild, guild_id)
    if guild is None:
        return None

    rows = await session.scalars(select(Subscription).where(Subscription.guild_id == guild_id))
    return GuildEnforcementState(
        guild_id=guild_id,
        subscriptions={row.pool_id: row.level for row in rows},
        enforcement_paused=guild.enforcement_paused,
    )


async def user_listings(session: AsyncSession, user_id: int) -> tuple[PoolListing, ...]:
    """Every live listing for this user, across every pool.

    Every pool, not just the subscribed ones: filtering to what a guild actually holds
    is `decide()`'s job, and giving it the whole picture is what lets it explain itself.
    """
    rows = await session.execute(
        select(Pool.id, Pool.name, Listing.reason)
        .join(Listing, Listing.pool_id == Pool.id)
        .where(Listing.user_id == user_id)
        .order_by(Pool.name)
    )
    return tuple(
        PoolListing(pool_id=pool_id, pool_name=pool_name, reason=reason)
        for pool_id, pool_name, reason in rows
    )


async def warned_pool_ids(
    session: AsyncSession, *, guild_id: int, user_id: int
) -> frozenset[int]:
    """Pools this user has already been warned about here.

    A `warned` outcome is permanent and survives leaves and rejoins, which is what keeps
    warnings to one per user per pool per guild.
    """
    rows = await session.scalars(
        select(EnforcementOutcome.pool_id).where(
            EnforcementOutcome.guild_id == guild_id,
            EnforcementOutcome.user_id == user_id,
            EnforcementOutcome.status == OutcomeStatus.WARNED,
        )
    )
    return frozenset(rows)


async def gather(
    session: AsyncSession,
    discord: DiscordPort,
    *,
    guild_id: int,
    user_id: int,
) -> EnforcementRequest | None:
    """Build the one question about this user in this guild.

    `None` when Timothy is not in the guild — there is nothing to enforce and nothing to
    record.

    Presence is left `False` until it is known to matter. `decide()` returns on paused
    and on not-listed before it ever reads `user_is_present`, so in those two cases the
    field cannot change the answer and resolving it would be a Discord call spent to
    learn nothing.
    """
    guild = await guild_state(session, guild_id)
    if guild is None:
        return None

    draft = EnforcementRequest(
        user_id=user_id,
        guild=guild,
        listings=await user_listings(session, user_id),
        user_is_present=False,
        has_exception=await session.get(GuildException, (guild_id, user_id)) is not None,
        already_warned_pool_ids=await warned_pool_ids(
            session, guild_id=guild_id, user_id=user_id
        ),
    )
    if guild.enforcement_paused or not subscribed_listings(draft):
        return draft

    member = await discord.fetch_member(guild_id=guild_id, user_id=user_id)
    return replace(draft, user_is_present=member is not None)


# -- targeting ---------------------------------------------------------------


async def guilds_subscribing_to(session: AsyncSession, pool_id: int) -> list[int]:
    """Every guild holding a subscription to this pool, at any level."""
    rows = await session.scalars(
        select(Subscription.guild_id)
        .where(Subscription.pool_id == pool_id)
        .order_by(Subscription.guild_id)
    )
    return list(rows)


async def users_listed_in(session: AsyncSession, pool_id: int) -> list[int]:
    """Every user this pool lists."""
    rows = await session.scalars(
        select(Listing.user_id).where(Listing.pool_id == pool_id).order_by(Listing.user_id)
    )
    return list(rows)


async def sweep_candidates(session: AsyncSession, guild_id: int) -> list[int]:
    """The users a sweep of this guild still has something to say about.

    Not everyone the guild's pools list — everyone for whom some subscribed pool has no
    settled outcome yet. In a guild that has been enforcing for a while that is almost
    nobody, which is what keeps the safety net from costing a `fetch_member` per listing
    per hour. A `failed` outcome makes a user a candidate again, which is how a ban that
    Discord refused an hour ago gets another chance.
    """
    rows = await session.scalars(
        select(distinct(Listing.user_id))
        .join(Subscription, Subscription.pool_id == Listing.pool_id)
        .outerjoin(
            EnforcementOutcome,
            (EnforcementOutcome.guild_id == Subscription.guild_id)
            & (EnforcementOutcome.user_id == Listing.user_id)
            & (EnforcementOutcome.pool_id == Listing.pool_id),
        )
        .where(
            Subscription.guild_id == guild_id,
            or_(
                EnforcementOutcome.status.is_(None),
                EnforcementOutcome.status.not_in(SETTLED),
            ),
        )
        .order_by(Listing.user_id)
    )
    return list(rows)


async def ban_level_pool_ids(
    session: AsyncSession, *, guild_id: int, user_id: int
) -> frozenset[int]:
    """Pools that still, right now, justify banning this user in this guild.

    A live listing in a pool the guild holds at `ban`. This is `still_justified` in
    :func:`~timothy_core.enforcement.decisions.decide_revert`: whatever went away, if one
    of these remains the ban stands on its own.
    """
    rows = await session.scalars(
        select(Listing.pool_id)
        .join(Subscription, Subscription.pool_id == Listing.pool_id)
        .where(
            Subscription.guild_id == guild_id,
            Subscription.level == SubscriptionLevel.BAN,
            Listing.user_id == user_id,
        )
    )
    return frozenset(rows)


async def is_listed_in_subscribed_pool(
    session: AsyncSession, *, guild_id: int, user_id: int
) -> bool:
    """Whether some pool this guild subscribes to lists this user.

    The question ADR 0006 turns the auto-exception hook on: it fires only where the
    unban would otherwise be undone.
    """
    found = await session.scalar(
        select(Listing.id)
        .join(Subscription, Subscription.pool_id == Listing.pool_id)
        .where(Subscription.guild_id == guild_id, Listing.user_id == user_id)
        .limit(1)
    )
    return found is not None
