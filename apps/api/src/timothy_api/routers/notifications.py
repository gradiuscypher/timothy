"""The channel where Timothy reports what it did in a guild.

One per guild, so setting it is a `PUT` rather than a create. Timothy does not check it
can post there: it has no way to know today whether it still can tomorrow, and phase 3's
enforcement records the `ForbiddenError` as an outcome, which is the durable answer.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from timothy_api import audit
from timothy_api.deps import Requires, SessionDep
from timothy_api.lookups import get_guild, not_found
from timothy_api.policy import Operation
from timothy_api.schemas import NotificationChannelRead, NotificationChannelSet, Snowflake
from timothy_core.actors import Actor
from timothy_core.db.models import NotificationChannel

router = APIRouter(prefix="/guilds/{guild_id}/notification-channel", tags=["notifications"])

Manager = Annotated[Actor, Depends(Requires(Operation.MANAGE_NOTIFICATION_CHANNEL))]

GuildId = Annotated[Snowflake, Path(description="A Discord guild ID.")]


@router.get("")
async def read_notification_channel(
    guild_id: GuildId, _actor: Manager, session: SessionDep
) -> NotificationChannelRead:
    """Where this guild's notifications go."""
    await get_guild(session, guild_id)
    channel = await session.get(NotificationChannel, guild_id)
    if channel is None:
        raise not_found(f"notification channel for guild {guild_id}")
    return NotificationChannelRead.of(channel)


@router.put("")
async def set_notification_channel(
    guild_id: GuildId,
    body: NotificationChannelSet,
    actor: Manager,
    session: SessionDep,
) -> NotificationChannelRead:
    """Point this guild's notifications at a channel."""
    await get_guild(session, guild_id)

    channel = await session.get(NotificationChannel, guild_id)
    if channel is None:
        channel = NotificationChannel(
            guild_id=guild_id, channel_id=body.channel_id, created_by=actor
        )
        session.add(channel)
    else:
        channel.channel_id = body.channel_id
        channel.created_by = actor

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.NOTIFICATION_CHANNEL_SET,
        target=audit.guild_target(guild_id),
        detail={"channel_id": str(body.channel_id)},
    )
    await session.commit()
    return NotificationChannelRead.of(channel)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_channel(
    guild_id: GuildId, actor: Manager, session: SessionDep
) -> None:
    """Stop reporting to a channel.

    Warn-level subscriptions with nowhere to report will record a failed outcome rather
    than a warned one, and so will be retried if a channel is set later.
    """
    await get_guild(session, guild_id)
    channel = await session.get(NotificationChannel, guild_id)
    if channel is None:
        raise not_found(f"notification channel for guild {guild_id}")

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.NOTIFICATION_CHANNEL_DELETE,
        target=audit.guild_target(guild_id),
    )
    await session.delete(channel)
    await session.commit()
