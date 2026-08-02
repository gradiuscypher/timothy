"""Guilds: Timothy's record of where it is, and whether it is enforcing there.

Registration is Timothy's own business — it follows the bot joining or leaving, with no
human in the loop and so no Discord permission to derive authority from. Those two
routes are the whole of :attr:`~timothy_api.policy.Requirement.SYSTEM`. Pausing and
resuming enforcement is the guild's own business, and needs an administrator there.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from timothy_api import audit, jobs
from timothy_api.deps import Requires, SessionDep, SettingsDep
from timothy_api.lookups import find_pool, get_guild
from timothy_api.policy import Operation
from timothy_api.schemas import GuildRead, GuildUpdate, Snowflake
from timothy_core.actors import Actor
from timothy_core.db.models import Guild, Subscription
from timothy_core.enums import SubscriptionLevel

router = APIRouter(prefix="/guilds", tags=["guilds"])

Registrar = Annotated[Actor, Depends(Requires(Operation.REGISTER_GUILD))]
GuildReader = Annotated[Actor, Depends(Requires(Operation.READ_GUILD))]
GuildManager = Annotated[Actor, Depends(Requires(Operation.MANAGE_GUILD_ENFORCEMENT))]

GuildId = Annotated[Snowflake, Path(description="A Discord guild ID.")]


async def _auto_subscribe(session: AsyncSession, guild: Guild, pool_name: str) -> None:
    """Subscribe a newly joined guild to the shared pool (ADR 0002).

    Preserves what the old bot did — every guild enforced `global` — without the reserved
    name that made it impossible to opt out. If the pool does not exist yet, nothing
    happens: there is no shared banlist to enforce.

    Only ever called for a guild being registered for the first time, so there is no
    existing subscription to collide with.
    """
    pool = await find_pool(session, pool_name)
    if pool is None:
        return

    session.add(
        Subscription(
            guild_id=guild.guild_id,
            pool_id=pool.id,
            level=SubscriptionLevel.BAN,
            created_by=Actor.system(),
        )
    )
    jobs.enqueue(
        session,
        jobs.JobKind.ENFORCE_SUBSCRIPTION,
        guild_id=guild.guild_id,
        pool_id=pool.id,
    )
    audit.record(
        session,
        actor=Actor.system(),
        action=audit.AuditAction.SUBSCRIPTION_SET,
        target=audit.guild_pool_target(guild_id=guild.guild_id, pool_name=pool.name),
        detail={"pool_id": pool.id, "level": SubscriptionLevel.BAN.value, "reason": "joined"},
    )


@router.put("/{guild_id}")
async def register_guild(
    guild_id: GuildId,
    actor: Registrar,
    session: SessionDep,
    settings: SettingsDep,
) -> GuildRead:
    """Record that Timothy is in a guild.

    Idempotent, because the bot re-announces its guilds every time the gateway
    reconnects. Only the first registration auto-subscribes; a guild that has since
    unsubscribed stays unsubscribed.
    """
    guild = await session.get(Guild, guild_id)
    if guild is not None:
        return GuildRead.of(guild)

    guild = Guild(guild_id=guild_id)
    session.add(guild)
    await session.flush()

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.GUILD_REGISTER,
        target=audit.guild_target(guild_id),
    )
    if settings.auto_subscribe_pool:
        await _auto_subscribe(session, guild, settings.auto_subscribe_pool)

    await session.commit()
    return GuildRead.of(guild)


@router.delete("/{guild_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deregister_guild(guild_id: GuildId, actor: Registrar, session: SessionDep) -> None:
    """Forget a guild Timothy has left.

    Its configuration cascades away with it. Its enforcement outcomes do not — they hold
    no foreign key, so a guild that re-adds Timothy still knows which of its bans were
    Timothy's.
    """
    guild = await get_guild(session, guild_id)
    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.GUILD_DEREGISTER,
        target=audit.guild_target(guild_id),
    )
    await session.delete(guild)
    await session.commit()


@router.get("/{guild_id}")
async def read_guild(guild_id: GuildId, _actor: GuildReader, session: SessionDep) -> GuildRead:
    """One guild's state."""
    return GuildRead.of(await get_guild(session, guild_id))


@router.patch("/{guild_id}")
async def update_guild(
    guild_id: GuildId, body: GuildUpdate, actor: GuildManager, session: SessionDep
) -> GuildRead:
    """Pause or resume enforcement in one guild.

    ADR 0007's per-guild rail: isolate one misbehaving guild without stopping the
    service. Resuming enqueues a catch-up, because everything that happened while paused
    deliberately recorded nothing.
    """
    guild = await get_guild(session, guild_id)
    was_paused = guild.enforcement_paused
    guild.enforcement_paused = body.enforcement_paused

    if was_paused and not body.enforcement_paused:
        jobs.enqueue(session, jobs.JobKind.ENFORCE_GUILD, guild_id=guild_id)

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.GUILD_ENFORCEMENT_SET,
        target=audit.guild_target(guild_id),
        detail={"enforcement_paused": body.enforcement_paused},
    )
    await session.commit()
    return GuildRead.of(guild)
