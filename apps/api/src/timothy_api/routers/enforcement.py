"""What Timothy has actually done in a guild.

`enforcement_outcomes` read back. Phase 6 wants this as per-guild enforcement history;
phase 3 wants it sooner, because it is the only way to see from outside whether a fan-out
landed, which pool a ban is attributed to, and what a `failed` row is waiting on.

A guild's own administrators, and no wider: this names the users their guild has banned
and why, which is not something the management guild's pool owners need in order to own
pools.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select

from timothy_api.deps import Requires, SessionDep
from timothy_api.lookups import get_guild
from timothy_api.policy import Operation
from timothy_api.schemas import EnforcementOutcomeRead, Snowflake
from timothy_core.actors import Actor
from timothy_core.db.models import EnforcementOutcome
from timothy_core.enums import OutcomeStatus

router = APIRouter(prefix="/guilds/{guild_id}/enforcement", tags=["enforcement"])

Reader = Annotated[Actor, Depends(Requires(Operation.READ_ENFORCEMENT))]

GuildId = Annotated[Snowflake, Path(description="A Discord guild ID.")]
StatusFilter = Annotated[
    OutcomeStatus | None,
    Query(description="Only outcomes with this status. `failed` is the interesting one."),
]


@router.get("")
async def list_enforcement_outcomes(
    guild_id: GuildId,
    _actor: Reader,
    session: SessionDep,
    status: StatusFilter = None,
) -> list[EnforcementOutcomeRead]:
    """Every enforcement Timothy has recorded in this guild, newest first."""
    await get_guild(session, guild_id)

    statement = select(EnforcementOutcome).where(EnforcementOutcome.guild_id == guild_id)
    if status is not None:
        statement = statement.where(EnforcementOutcome.status == status)

    rows = await session.scalars(statement.order_by(EnforcementOutcome.attempted_at.desc()))
    return [EnforcementOutcomeRead.of(row) for row in rows]
