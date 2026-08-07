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

import logging
from typing import TYPE_CHECKING, Final

from timothy_api import usernames
from timothy_api.enforcement import outcomes, revert, state
from timothy_api.enforcement.engine import Run
from timothy_api.enforcement.retry import with_backoff
from timothy_api.jobs import JobKind
from timothy_core.db.models import Listing
from timothy_core.enums import OutcomeStatus
from timothy_core.ports.discord import DiscordError

log = logging.getLogger(__name__)

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


# -- names -------------------------------------------------------------------


async def backfill_user_names(ctx: JobContext, _payload: dict[str, int]) -> None:
    """Ask Discord what a batch of unnamed user IDs are called (ADR 0017).

    Takes nothing from the payload, including the batch size: an operator who lowers
    `USERNAME_BACKFILL_BATCH` and restarts expects the next round *to run* to obey it,
    not the next one to be queued.

    The only handler here that decides nothing and changes nothing at Discord. Everything
    it writes is a label the UI draws and no code reads.

    Three properties, each of which is the difference between a cheap job and a problem:

    * **Serial, one lookup at a time**, like every other Discord call the worker makes.
      A batch is a few hundred requests against a global rate limit shared with the bans
      that matter, and those come first by being on the same single worker.
    * **A user Discord has never heard of is recorded as such** rather than skipped, so
      tomorrow's round asks about somebody new instead of the same dead IDs.
    * **Committed per lookup.** A batch interrupted half way keeps the names it did
      learn; nothing here is worth holding a SQLite write transaction open for.

    A rate limit or an outage that outlasts :func:`~timothy_api.enforcement.retry`'s
    backoff ends the batch rather than failing the job. The names already learned are
    kept, and what is left is picked up by the next round — a backfill is never urgent,
    and retrying it would only spend more of a budget Discord has just said is empty.
    """
    limit = ctx.settings.username_backfill_batch
    async with ctx.sessions() as session:
        user_ids = await usernames.without_names(session, limit=limit)

    if not user_ids:
        return

    found = 0
    for position, user_id in enumerate(user_ids):
        try:
            user = await with_backoff(
                lambda user_id=user_id: ctx.enforcer.discord.fetch_user(user_id=user_id),
                sleep=ctx.enforcer.sleep,
            )
        except DiscordError:
            log.warning(
                "user name backfill stopped after %d of %d lookup(s)", position, len(user_ids)
            )
            break

        async with ctx.sessions() as session:
            if user is None:
                await usernames.record_missing(session, user_id=user_id)
            else:
                found += 1
                await usernames.record(session, user_id=user_id, name=user.name)
            await session.commit()

    log.info("user name backfill: %d name(s) from %d lookup(s)", found, len(user_ids))


# -- shared shapes -----------------------------------------------------------


PROGRESS_EVERY: Final = 100
"""How often a fan-out says where it has got to. Small enough that a wedged sweep is
obvious within a minute or so of Discord calls, large enough that a big one adds tens of
lines rather than thousands — the per-pair detail is `engine`'s DEBUG line, not this."""


async def _enforce_pairs(ctx: JobContext, pairs: Iterable[tuple[int, int]]) -> None:
    """Ask about each (guild, user) in turn, one transaction each.

    One `Run` across the whole fan-out, because the circuit breaker's threshold is per
    guild per run and this is the run.

    The pairs are materialised so the total can be logged before the work starts. They
    come from `state` queries that already hold the same IDs in a list, so this costs a
    second list of tuples and buys the one number that says whether a long job is large
    or stuck — the question `worker`'s start/finish pair cannot answer on its own.
    """
    run = Run()
    todo = list(pairs)
    log.info("enforcing %d (guild, user) pair(s)", len(todo), extra={"pair_total": len(todo)})
    halted = 0
    for done, (guild_id, user_id) in enumerate(todo, start=1):
        if run.is_halted(guild_id):
            halted += 1
            continue
        async with ctx.sessions() as session:
            await ctx.enforcer.enforce(session, run, guild_id=guild_id, user_id=user_id)
            await session.commit()
        if done % PROGRESS_EVERY == 0:
            log.info(
                "enforced %d of %d pair(s)",
                done,
                len(todo),
                extra={"pair_done": done, "pair_total": len(todo)},
            )
    if halted:
        log.warning(
            "skipped %d of %d pair(s): the breaker had halted their guild",
            halted,
            len(todo),
            extra={"pair_halted": halted, "pair_total": len(todo)},
        )


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
    JobKind.BACKFILL_USER_NAMES: backfill_user_names,
}
"""Every kind has a handler, and the test suite asserts that stays true — an unhandled
kind would be a job that fails its way to `failed` with a `KeyError` for a reason."""
