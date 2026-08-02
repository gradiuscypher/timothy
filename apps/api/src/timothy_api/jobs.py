"""Enqueuing the enforcement work a mutation implies.

Creating a listing enqueues enforcement (ADR 0004); removing one with `revert` set
enqueues a revert (ADR 0005). :mod:`timothy_api.enforcement.handlers` is what drains
these, one function per kind.

Enqueuing happens in the mutation's own transaction. A job committed separately from the
change that justifies it is a job that can be lost after the change lands, or run before
it: either way Timothy would enforce a world that never existed.

The payloads are deliberately thin. They name what changed, not what to do about it —
which guilds a new listing reaches is a question about subscriptions at the moment the
worker runs, not at the moment a moderator typed the command, and answering it here
would bake a stale fan-out into the queue.
"""

from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from timothy_core.db.models import Job


class JobKind(StrEnum):
    """The work a mutation can imply."""

    ENFORCE_LISTING = "enforce_listing"
    """`{listing_id}` — a listing appeared. Reaches every guild subscribing to its pool."""

    ENFORCE_SUBSCRIPTION = "enforce_subscription"
    """`{guild_id, pool_id}` — a guild subscribed, or raised warn to ban."""

    ENFORCE_GUILD = "enforce_guild"
    """`{guild_id}` — enforcement was un-paused; catch the guild up."""

    ENFORCE_GUILD_USER = "enforce_guild_user"
    """`{guild_id, user_id}` — an exception was removed, so this user is enforceable
    again in this guild."""

    REVERT_LISTING = "revert_listing"
    """`{pool_id, user_id}` — a listing went away and the caller asked for a revert.
    Carries IDs rather than a listing row because the row is already gone; the
    `enforcement_outcomes` that make the revert safe hold no foreign keys precisely so
    they outlive it."""

    REVERT_SUBSCRIPTION = "revert_subscription"
    """`{guild_id, pool_id}` — a guild unsubscribed and asked for a revert."""

    REVERT_POOL = "revert_pool"
    """`{pool_id}` — a pool was deleted and the caller asked for a revert."""

    REVERT_GUILD_USER = "revert_guild_user"
    """`{guild_id, user_id}` — a guild vouched for someone Timothy had already banned
    there, and asked for that ban back."""


def enqueue(session: AsyncSession, kind: JobKind, **payload: int) -> Job:
    """Add one job to the caller's transaction.

    Not `async` and not committing, for the same reason
    :func:`timothy_api.audit.record` is not.
    """
    job = Job(kind=kind.value, payload=dict(payload))
    session.add(job)
    return job
