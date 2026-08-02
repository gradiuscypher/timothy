"""Exceptions: a guild's declaration that a user is never to be banned by Timothy there.

Guild-wide, never scoped to one pool (ADR 0006), and suppressing warnings as well as
bans — the warn copy tells a moderator a ban would have happened, which is exactly what
an exception says will never happen here.

The exception Timothy creates for itself after a moderator's manual unban is not this
route. That one follows a gateway event, decides through
:func:`timothy_core.enforcement.decisions.should_except_after_unban`, and lives in
:mod:`timothy_api.routers.events`. This route is for a human asking directly, and a human
needs `ADMINISTRATOR` in the guild.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import select

from timothy_api import audit, jobs
from timothy_api.deps import Requires, SessionDep
from timothy_api.lookups import conflict, get_guild, not_found
from timothy_api.policy import Operation
from timothy_api.schemas import ExceptionCreate, ExceptionRead, Snowflake
from timothy_core.actors import Actor
from timothy_core.db.models import GuildException

router = APIRouter(prefix="/guilds/{guild_id}/exceptions", tags=["exceptions"])

Manager = Annotated[Actor, Depends(Requires(Operation.MANAGE_EXCEPTIONS))]

GuildId = Annotated[Snowflake, Path(description="A Discord guild ID.")]
UserId = Annotated[Snowflake, Path(description="A Discord user ID.")]
Revert = Annotated[
    bool,
    Query(
        description=(
            "Also lift the ban Timothy has already issued this user here, if it issued "
            "one. Off by default, like every other revert (ADR 0005): an exception is "
            "read as 'from now on', and only bans with a recorded outcome are touched."
        )
    ),
]


@router.get("")
async def list_exceptions(
    guild_id: GuildId, _actor: Manager, session: SessionDep
) -> list[ExceptionRead]:
    """Everyone this guild has vouched for."""
    await get_guild(session, guild_id)
    rows = await session.scalars(
        select(GuildException)
        .where(GuildException.guild_id == guild_id)
        .order_by(GuildException.created_at)
    )
    return [ExceptionRead.of(row) for row in rows]


@router.put("/{user_id}", status_code=status.HTTP_201_CREATED)
async def create_exception(
    guild_id: GuildId,
    user_id: UserId,
    body: ExceptionCreate,
    actor: Manager,
    session: SessionDep,
    *,
    revert: Revert = False,
) -> ExceptionRead:
    """Vouch for a user in this guild.

    Whether an exception lifts a ban Timothy has *already* issued was left open by phase
    2, and this is the answer: it does, but only when asked. That keeps every revert in
    the codebase the same shape — opt-in, defaulting off, and never touching a ban
    without a recorded outcome (ADR 0005). Creating an exception silently would make it
    the one action that unbans people as a side effect, and the flow a moderator actually
    has for "let this person back in" is the unban itself, which ADR 0006's hook already
    turns into an exception.
    """
    await get_guild(session, guild_id)
    if await session.get(GuildException, (guild_id, user_id)) is not None:
        raise conflict(f"already excepted in guild {guild_id}: {user_id}")

    exception = GuildException(
        guild_id=guild_id,
        user_id=user_id,
        reason=body.reason,
        created_by=actor,
    )
    session.add(exception)
    await session.flush()

    if revert:
        jobs.enqueue(
            session,
            jobs.JobKind.REVERT_GUILD_USER,
            guild_id=guild_id,
            user_id=user_id,
        )

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.EXCEPTION_CREATE,
        target=audit.guild_user_target(guild_id=guild_id, user_id=user_id),
        detail={"reason": body.reason, "revert": revert},
    )
    await session.commit()
    return ExceptionRead.of(exception)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exception(
    guild_id: GuildId, user_id: UserId, actor: Manager, session: SessionDep
) -> None:
    """Withdraw the vouch, and let enforcement look at this user again."""
    await get_guild(session, guild_id)
    exception = await session.get(GuildException, (guild_id, user_id))
    if exception is None:
        raise not_found(f"exception: {user_id} in guild {guild_id}")

    jobs.enqueue(session, jobs.JobKind.ENFORCE_GUILD_USER, guild_id=guild_id, user_id=user_id)
    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.EXCEPTION_DELETE,
        target=audit.guild_user_target(guild_id=guild_id, user_id=user_id),
    )
    await session.delete(exception)
    await session.commit()
