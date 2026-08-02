"""One function per `JobKind`: what a thing that changed actually implies.

The payloads name what changed and never what to do about it, so this is where the
fan-out is worked out — against the subscriptions, listings and outcomes as they stand
*now*, not as they stood when a moderator typed the command. A listing created an hour
ago reaches the guilds subscribing today.

Each (guild, user) question gets its own session and its own commit. Holding one
transaction open across a fan-out of a hundred guilds would mean a single Discord
timeout at guild ninety-nine discards ninety-eight bans that really were issued, and
SQLite has one writer (ADR 0003) — a long write transaction is one nothing else can
interleave with.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from timothy_api.enforcement import outcomes, revert, state
from timothy_api.enforcement.engine import Run
from timothy_api.jobs import JobKind
from timothy_core.db.models import Listing
from timothy_core.enums import OutcomeStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from timothy_api.enforcement.worker import JobContext

type Handler = Callable[["JobContext", dict[str, int]], Awaitable[None]]


# -- enforcement -------------------------------------------------------------


async def enforce_listing(ctx: JobContext, payload: dict[str, int]) -> None:
    """A listing appeared. Reach every guild subscribing to its pool."""
    async with ctx.sessions() as session:
        listing = await session.get(Listing, payload["listing_id"])
        if listing is None:
            return  # Removed again before the worker got to it. Nothing to enforce.
        pool_id, user_id = listing.pool_id, listing.user_id
        guild_ids = await state.guilds_subscribing_to(session, pool_id)

    await _enforce_pairs(ctx, ((guild_id, user_id) for guild_id in guild_ids))


async def enforce_subscription(ctx: JobContext, payload: dict[str, int]) -> None:
    """A guild subscribed, or raised warn to ban. Catch it up on the whole pool.

    The fan-out most likely to trip the circuit breaker, and deliberately so: subscribing
    to a large pool is the one routine action that bans many people at once.
    """
    guild_id = payload["guild_id"]
    async with ctx.sessions() as session:
        user_ids = await state.users_listed_in(session, payload["pool_id"])

    await _enforce_pairs(ctx, ((guild_id, user_id) for user_id in user_ids))


async def enforce_guild(ctx: JobContext, payload: dict[str, int]) -> None:
    """Sweep one guild: everyone a subscribed pool lists and nothing has settled yet.

    Both the hourly safety net and the catch-up after a pause is lifted. Everything that
    happened while paused recorded nothing on purpose, so there is nothing to clear
    first — the candidates come back on their own.
    """
    guild_id = payload["guild_id"]
    async with ctx.sessions() as session:
        user_ids = await state.sweep_candidates(session, guild_id)

    await _enforce_pairs(ctx, ((guild_id, user_id) for user_id in user_ids))


async def enforce_guild_user(ctx: JobContext, payload: dict[str, int]) -> None:
    """Look at one user in one guild again.

    Queued when an exception is withdrawn and when a user joins a guild. Both mean "the
    settled answer may have stopped being true", so any `skipped_exception` recorded
    against this pair is cleared first — it is the one settled status that a change
    outside enforcement can invalidate, and leaving it would have the sweep skip this
    user forever.
    """
    guild_id, user_id = payload["guild_id"], payload["user_id"]
    async with ctx.sessions() as session:
        await outcomes.clear(
            session,
            guild_id=guild_id,
            user_id=user_id,
            statuses=[OutcomeStatus.SKIPPED_EXCEPTION],
        )
        await session.commit()

    await _enforce_pairs(ctx, [(guild_id, user_id)])


# -- reverts -----------------------------------------------------------------


async def revert_listing(ctx: JobContext, payload: dict[str, int]) -> None:
    """A listing went away and the caller asked for the bans back.

    The listing row is already gone; the enforcement outcomes it caused are not, because
    they hold no foreign keys (ADR 0005).
    """
    pool_id, user_id = payload["pool_id"], payload["user_id"]
    async with ctx.sessions() as session:
        pairs = await outcomes.banned_pairs_for_pool(session, pool_id, user_id=user_id)

    await _revert_pairs(ctx, pairs, pool_id)


async def revert_subscription(ctx: JobContext, payload: dict[str, int]) -> None:
    """A guild unsubscribed and asked for the bans this pool was holding up."""
    guild_id, pool_id = payload["guild_id"], payload["pool_id"]
    async with ctx.sessions() as session:
        user_ids = await outcomes.banned_users_in(session, guild_id=guild_id, pool_id=pool_id)

    await _revert_pairs(ctx, [(guild_id, user_id) for user_id in user_ids], pool_id)


async def revert_pool(ctx: JobContext, payload: dict[str, int]) -> None:
    """A pool was deleted and the caller asked for the bans back, everywhere."""
    pool_id = payload["pool_id"]
    async with ctx.sessions() as session:
        pairs = await outcomes.banned_pairs_for_pool(session, pool_id)

    await _revert_pairs(ctx, pairs, pool_id)


async def revert_guild_user(ctx: JobContext, payload: dict[str, int]) -> None:
    """A guild vouched for someone Timothy had already banned there.

    Opt-in, like every other revert: `PUT .../exceptions/{user}?revert=true`. See
    :func:`timothy_api.enforcement.revert.revert_for_exception` for why this one does not
    ask `decide_revert`.
    """
    guild_id, user_id = payload["guild_id"], payload["user_id"]
    async with ctx.sessions() as session:
        await revert.revert_for_exception(
            session, ctx.enforcer, guild_id=guild_id, user_id=user_id
        )
        await session.commit()


# -- shared shapes -----------------------------------------------------------


async def _enforce_pairs(ctx: JobContext, pairs: Iterable[tuple[int, int]]) -> None:
    """Ask about each (guild, user) in turn, one transaction each.

    One `Run` across the whole fan-out, because the circuit breaker's threshold is per
    guild per run and this is the run.
    """
    run = Run()
    for guild_id, user_id in pairs:
        if run.is_halted(guild_id):
            continue
        async with ctx.sessions() as session:
            await ctx.enforcer.enforce(session, run, guild_id=guild_id, user_id=user_id)
            await session.commit()


async def _revert_pairs(
    ctx: JobContext, pairs: Iterable[tuple[int, int]], pool_id: int
) -> None:
    for guild_id, user_id in pairs:
        async with ctx.sessions() as session:
            await revert.revert_user(
                session,
                ctx.enforcer,
                guild_id=guild_id,
                user_id=user_id,
                revoked_pool_ids=frozenset({pool_id}),
            )
            await session.commit()


HANDLERS: dict[JobKind, Handler] = {
    JobKind.ENFORCE_LISTING: enforce_listing,
    JobKind.ENFORCE_SUBSCRIPTION: enforce_subscription,
    JobKind.ENFORCE_GUILD: enforce_guild,
    JobKind.ENFORCE_GUILD_USER: enforce_guild_user,
    JobKind.REVERT_LISTING: revert_listing,
    JobKind.REVERT_SUBSCRIPTION: revert_subscription,
    JobKind.REVERT_POOL: revert_pool,
    JobKind.REVERT_GUILD_USER: revert_guild_user,
}
"""Every kind has a handler, and the test suite asserts that stays true — an unhandled
kind would be a job that fails its way to `failed` with a `KeyError` for a reason."""
