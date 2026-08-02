"""Lifting bans Timothy issued, once the thing that justified them is gone.

ADR 0005, and its one hard rule: **a guild's own bans are never touched.** The only
evidence Timothy has that a ban is its own is a `banned` enforcement outcome, so no
outcome means no unban, no matter what the listings say. That is why the outcomes table
holds no foreign keys — the pool that caused the ban is often already deleted by the time
anyone asks.

Two deliberate choices beyond the ADR:

**A revert ignores the per-guild pause.** The pause stops Timothy acting *against* a
guild's members; a revert only ever readmits them. Honouring it here would disable the
remedy at exactly the moment it is needed, since the usual reason a guild is paused is
that the circuit breaker just tripped on a bad bulk listing — and deleting that listing
with `revert` is the fix.

**A successful unban clears every `banned` row for that user in that guild**, not only
the rows for the pool that went away. Once the ban is lifted there is nothing left for
any of them to attribute, and a survivor would have a later revert try to unban someone
who is already back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from timothy_api import audit
from timothy_api.enforcement import outcomes, state
from timothy_api.enforcement.retry import with_backoff
from timothy_core.actors import Actor
from timothy_core.enforcement.decisions import RevertVerdict, decide_revert
from timothy_core.enums import OutcomeStatus
from timothy_core.ports.discord import DiscordError, NotFoundError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from timothy_api.enforcement.engine import Enforcer

REVERT_REASON = "Timothy: no longer listed in a pool this server enforces"
EXCEPTION_REASON = "Timothy: an exception was created for this user in this server"


async def revert_user(
    session: AsyncSession,
    enforcer: Enforcer,
    *,
    guild_id: int,
    user_id: int,
    revoked_pool_ids: frozenset[int],
) -> RevertVerdict:
    """Consider lifting one ban, after `revoked_pool_ids` stopped justifying it.

    Returns the verdict so a caller can count what it did, and so the tests can assert on
    the reasoning rather than only on the outcome.
    """
    attributed = await outcomes.banned_pool_ids(session, guild_id=guild_id, user_id=user_id)
    still = await state.ban_level_pool_ids(session, guild_id=guild_id, user_id=user_id)

    verdict = decide_revert(
        banned_by_timothy=bool(attributed & revoked_pool_ids),
        still_justified=bool(still),
    )
    if verdict is RevertVerdict.NOT_ATTRIBUTABLE:
        return verdict

    if verdict is RevertVerdict.STILL_JUSTIFIED:
        # The ban stands, but these pools are no longer part of why.
        await outcomes.clear(
            session,
            guild_id=guild_id,
            user_id=user_id,
            pool_ids=revoked_pool_ids,
            statuses=[OutcomeStatus.BANNED],
        )
        return verdict

    await _lift(
        session,
        enforcer,
        guild_id=guild_id,
        user_id=user_id,
        reason=REVERT_REASON,
        detail={"revoked_pool_ids": sorted(revoked_pool_ids)},
    )
    return verdict


async def revert_for_exception(
    session: AsyncSession, enforcer: Enforcer, *, guild_id: int, user_id: int
) -> bool:
    """Lift a ban because the guild has just vouched for this user.

    Attribution still applies — a guild's own ban is still not Timothy's to lift. What
    does not apply is `still_justified`: an exception *is* the guild's decision that its
    subscriptions do not reach this person, so the listings that would otherwise hold the
    ban up are precisely what is being overridden. Running this through `decide_revert`
    would answer STILL_JUSTIFIED every time and make the flag do nothing.

    The `banned` rows become `skipped_exception`, which is what the same user would have
    recorded had the exception existed first.
    """
    attributed = await outcomes.banned_pool_ids(session, guild_id=guild_id, user_id=user_id)
    if not attributed:
        return False

    lifted = await _lift(
        session,
        enforcer,
        guild_id=guild_id,
        user_id=user_id,
        reason=EXCEPTION_REASON,
        detail={"cause": "exception", "pool_ids": sorted(attributed)},
    )
    if not lifted:
        return False

    for pool_id in sorted(attributed):
        await outcomes.record(
            session,
            guild_id=guild_id,
            user_id=user_id,
            pool_id=pool_id,
            status=OutcomeStatus.SKIPPED_EXCEPTION,
            reason="an exception was created after the ban",
        )
    return True


async def _lift(  # noqa: PLR0913 — the two callers differ only in what they say and log
    session: AsyncSession,
    enforcer: Enforcer,
    *,
    guild_id: int,
    user_id: int,
    reason: str,
    detail: dict[str, object],
) -> bool:
    """Unban, mark the unban as Timothy's own, and drop the attribution.

    The marker goes down *before* the call. Timothy's unban raises `GUILD_BAN_REMOVE` on
    the gateway just as a moderator's does, and ADR 0006's hook would turn it into a
    permanent exception for the user it just readmitted (ADR 0005's second consequence).
    """
    if enforcer.settings.dry_run:
        audit.record(
            session,
            actor=Actor.system(),
            action=audit.AuditAction.ENFORCEMENT_DRY_RUN,
            target=audit.guild_user_target(guild_id=guild_id, user_id=user_id),
            detail={"would": "revert", **detail},
        )
        return False

    enforcer.self_unbans.mark(guild_id=guild_id, user_id=user_id)
    try:
        await with_backoff(
            lambda: enforcer.discord.unban(guild_id=guild_id, user_id=user_id, reason=reason),
            sleep=enforcer.sleep,
        )
    except NotFoundError:
        # Already not banned there — somebody got to it first. The attribution is stale
        # either way, so clearing it is still right.
        pass
    except DiscordError as error:
        audit.record(
            session,
            actor=Actor.system(),
            action=audit.AuditAction.ENFORCEMENT_FAILED,
            target=audit.guild_user_target(guild_id=guild_id, user_id=user_id),
            detail={"action": "revert", "error": str(error)},
        )
        return False

    await outcomes.clear(
        session,
        guild_id=guild_id,
        user_id=user_id,
        statuses=[OutcomeStatus.BANNED],
    )
    audit.record(
        session,
        actor=Actor.system(),
        action=audit.AuditAction.ENFORCEMENT_REVERT,
        target=audit.guild_user_target(guild_id=guild_id, user_id=user_id),
        detail=detail,
    )
    return True
