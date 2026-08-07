"""The append-only record of every action taken through Timothy, and who took it.

One row per mutation, written into the same transaction as the mutation itself. That is
the whole design: an audit row that could be committed separately is an audit row that
can go missing exactly when it matters.

Unrelated to a Sweep, despite what "audit" means elsewhere in Discord bots — see
CONTEXT.md.
"""

from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from timothy_core.actors import Actor
from timothy_core.db.models import AuditLogEntry


class AuditAction(StrEnum):
    """What was done. `<subject>.<verb>`, so a reader can filter by subject."""

    POOL_CREATE = "pool.create"
    POOL_UPDATE = "pool.update"
    POOL_DELETE = "pool.delete"

    LISTING_CREATE = "listing.create"
    LISTING_DELETE = "listing.delete"

    SUBSCRIPTION_SET = "subscription.set"
    SUBSCRIPTION_DELETE = "subscription.delete"

    EXCEPTION_CREATE = "exception.create"
    EXCEPTION_DELETE = "exception.delete"

    NOTIFICATION_CHANNEL_SET = "notification_channel.set"
    NOTIFICATION_CHANNEL_DELETE = "notification_channel.delete"

    GUILD_REGISTER = "guild.register"
    GUILD_DEREGISTER = "guild.deregister"
    GUILD_ENFORCEMENT_SET = "guild.enforcement_set"

    # Timothy's own actions. The audit log covers these too (CONTEXT.md) — they are the
    # ones nobody typed, and so the ones a moderator most needs to be able to look up.
    ENFORCEMENT_BAN = "enforcement.ban"
    ENFORCEMENT_WARN = "enforcement.warn"
    ENFORCEMENT_FAILED = "enforcement.failed"
    ENFORCEMENT_REVERT = "enforcement.revert"
    ENFORCEMENT_BREAKER_TRIPPED = "enforcement.breaker_tripped"

    JOB_RUN_NOW = "job.run_now"
    """An operator pulled a queued job forward to run at once."""

    JOB_CANCEL = "job.cancel"
    """An operator dropped a queued job. Recorded because it is the one way work Timothy
    had decided to do stops happening without anybody being told — the sweep would have
    swept, and now it will not until the next round."""

    ENFORCEMENT_DRY_RUN = "enforcement.dry_run"
    """What Timothy *would* have done. In dry run this is the only record there is — the
    durable `enforcement_outcomes` stay empty, because an outcome is an attribution
    claim and nothing was attributable. Phase 5 diffs these against the old bot."""


def pool_target(name: str) -> str:
    """`pool:<name>` — named, not numbered, because the name is what a human searched for."""
    return f"pool:{name}"


def listing_target(*, pool_name: str, user_id: int) -> str:
    """`listing:<pool>/<user>`."""
    return f"listing:{pool_name}/{user_id}"


def guild_target(guild_id: int) -> str:
    """`guild:<id>`."""
    return f"guild:{guild_id}"


def guild_user_target(*, guild_id: int, user_id: int) -> str:
    """`guild:<id>/user:<id>` — for the things scoped to one user in one guild."""
    return f"guild:{guild_id}/user:{user_id}"


def job_target(job_id: int) -> str:
    """`job:<id>` — the queue row an operator acted on."""
    return f"job:{job_id}"


def guild_pool_target(*, guild_id: int, pool_name: str) -> str:
    """`guild:<id>/pool:<name>` — for subscriptions."""
    return f"guild:{guild_id}/pool:{pool_name}"


def record(
    session: AsyncSession,
    *,
    actor: Actor,
    action: AuditAction,
    target: str,
    detail: dict[str, object] | None = None,
) -> AuditLogEntry:
    """Add one audit row to the caller's transaction.

    Deliberately not `async` and deliberately not committing: it joins whatever the
    handler is already doing, and is durable exactly when that is.
    """
    entry = AuditLogEntry(
        actor=actor,
        action=action.value,
        target=target,
        detail=detail,
    )
    session.add(entry)
    return entry
