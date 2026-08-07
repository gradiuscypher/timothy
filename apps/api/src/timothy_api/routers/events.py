"""What the gateway saw. The bot relays; the backend decides.

Two events matter (PLAN.md, phase 4). A member joining is ADR 0004's "banned at the door"
— enforcement is reactive, so a user listed while absent is dealt with when they turn up.
A ban being lifted is ADR 0006's hook, and it is the more delicate of the two:

* Timothy's own unbans arrive here exactly as a moderator's do. Left alone, ADR 0006
  would grant a permanent exception to every user a revert just readmitted, making the
  next enforcement of that listing a no-op forever (ADR 0005's second consequence). The
  revert path marks its unbans and this claims the marker.
* The auto-exception is Timothy's own action, and `Requirement.SYSTEM` is refused
  everything a human owns. So this must not — and does not — call
  `PUT /guilds/{id}/exceptions/{user}`, which requires an administrator. It decides with
  :func:`~timothy_core.enforcement.decisions.should_except_after_unban` and writes the
  row itself.

Both routes answer once the decision is recorded, not once Discord has been called: the
gateway must not be kept waiting on a fan-out. Enforcement goes through the queue like
everything else.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from timothy_api import audit, jobs, usernames
from timothy_api.deps import Requires, SessionDep
from timothy_api.enforcement import outcomes, state
from timothy_api.enforcement.selfunbans import SelfUnbans
from timothy_api.policy import Operation
from timothy_api.schemas import EventAck, GatewayEvent
from timothy_core.actors import Actor
from timothy_core.db.models import Guild, GuildException
from timothy_core.enforcement.decisions import should_except_after_unban
from timothy_core.enums import OutcomeStatus

router = APIRouter(prefix="/events", tags=["events"])

Relay = Annotated[Actor, Depends(Requires(Operation.RELAY_EVENT))]


def get_self_unbans(request: Request) -> SelfUnbans:
    """The unbans Timothy has issued and not yet seen come back."""
    self_unbans: SelfUnbans = request.app.state.self_unbans
    return self_unbans


SelfUnbansDep = Annotated[SelfUnbans, Depends(get_self_unbans)]


async def _remember_name(session: SessionDep, body: GatewayEvent) -> None:
    """Keep whatever name the event carried, so the UI can stop showing a bare ID.

    A passenger on the transaction the handler was going to commit anyway, and never a
    reason to commit one it would not have. An event from a bot too old to send a name is
    handled by there being nothing to remember.
    """
    if body.username is not None:
        await usernames.record(session, user_id=body.user_id, name=body.username)


@router.post("/member-join", status_code=status.HTTP_202_ACCEPTED)
async def member_joined(body: GatewayEvent, _actor: Relay, session: SessionDep) -> EventAck:
    """A user joined a guild. Enforce against them there.

    Queued rather than done inline. The work is the same as any other enforcement, and
    routing it through the queue is what gives it the retries and the circuit breaker.
    """
    if await session.get(Guild, body.guild_id) is None:
        return EventAck(action="ignored: not a guild Timothy is in")

    await _remember_name(session, body)
    jobs.enqueue(
        session,
        jobs.JobKind.ENFORCE_GUILD_USER,
        guild_id=body.guild_id,
        user_id=body.user_id,
    )
    await session.commit()
    return EventAck(action="enforcement queued")


@router.post("/ban-remove", status_code=status.HTTP_202_ACCEPTED)
async def ban_removed(
    body: GatewayEvent,
    _actor: Relay,
    session: SessionDep,
    self_unbans: SelfUnbansDep,
) -> EventAck:
    """A ban was lifted in a guild. Decide whether it should stick.

    Whoever lifted it, any `banned` outcome for this user here has stopped being true and
    is cleared. Leaving it would have a later revert try to unban somebody who is already
    back, and would let the sweep believe this user is settled.
    """
    if await session.get(Guild, body.guild_id) is None:
        return EventAck(action="ignored: not a guild Timothy is in")

    await _remember_name(session, body)

    if self_unbans.claim(guild_id=body.guild_id, user_id=body.user_id):
        # The decision is "do nothing", but the name still arrived and is worth keeping,
        # so this one early return commits where it used to have nothing to write.
        await session.commit()
        return EventAck(action="ignored: Timothy's own revert")

    await outcomes.clear(
        session,
        guild_id=body.guild_id,
        user_id=body.user_id,
        statuses=[OutcomeStatus.BANNED],
    )

    listed = await state.is_listed_in_subscribed_pool(
        session, guild_id=body.guild_id, user_id=body.user_id
    )
    if not should_except_after_unban(
        unban_was_timothys_own=False, listed_in_subscribed_pool=listed
    ):
        await session.commit()
        return EventAck(action="no exception: not listed in a pool this guild enforces")

    existing = await session.get(GuildException, (body.guild_id, body.user_id))
    if existing is not None:
        await session.commit()
        return EventAck(action="no exception: one already exists")

    session.add(
        GuildException(
            guild_id=body.guild_id,
            user_id=body.user_id,
            reason="created automatically after a manual unban",
            created_by=Actor.system(),
        )
    )
    audit.record(
        session,
        actor=Actor.system(),
        action=audit.AuditAction.EXCEPTION_CREATE,
        target=audit.guild_user_target(guild_id=body.guild_id, user_id=body.user_id),
        detail={"cause": "manual unban"},
    )
    await session.commit()
    return EventAck(action="exception created")
