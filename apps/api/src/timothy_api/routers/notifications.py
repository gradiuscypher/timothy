"""The channel where Timothy reports what it did in a guild.

One per guild, so setting it is a `PUT` rather than a create.

Two questions are asked about a nominated channel, and they are not the same question.

**Does this guild own it?** Checked, here, once. `MANAGE_NOTIFICATION_CHANNEL` is
`TARGET_GUILD_ADMIN` — an administrator of any member server, not one of the few trusted
pool managers — and the ID in the body is otherwise stored and posted to unverified. An
administrator of guild A naming a channel in guild B would have Timothy carry A's warn
notices into B, which names users present in A to a server that has no business knowing.
A channel ID is bound to its guild for the channel's whole life, so asking once is
asking for good: this is settled at configuration time and never revisited.

**Can Timothy post there?** Deliberately not checked. It has no way to know today
whether it still can tomorrow, and enforcement records the `ForbiddenError` as an
outcome, which is the durable answer. Ownership is a fact; permission is a race.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from timothy_api import audit
from timothy_api.deps import DiscordDep, Requires, SessionDep
from timothy_api.lookups import get_guild, not_found
from timothy_api.policy import Operation
from timothy_api.schemas import NotificationChannelRead, NotificationChannelSet, Snowflake
from timothy_core.actors import Actor
from timothy_core.db.models import NotificationChannel
from timothy_core.ports.discord import DiscordPort

router = APIRouter(prefix="/guilds/{guild_id}/notification-channel", tags=["notifications"])

Manager = Annotated[Actor, Depends(Requires(Operation.MANAGE_NOTIFICATION_CHANNEL))]

GuildId = Annotated[Snowflake, Path(description="A Discord guild ID.")]


def _refused(detail: str) -> HTTPException:
    """The channel is real enough, but not one this guild may be told to use."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def _owned_by(discord: DiscordPort, *, guild_id: int, channel_id: int) -> None:
    """Refuse a channel this guild does not own, or that nothing can be posted to.

    Raises:
        HTTPException: 404 if Timothy cannot see a channel with that ID at all, 422 if
            it can but the channel belongs elsewhere or is a container rather than a
            place to say something.
    """
    channel = await discord.fetch_channel(channel_id=channel_id)
    if channel is None:
        raise not_found(f"channel: {channel_id}")
    if channel.guild_id != guild_id:
        # Deliberately does not say which guild does own it: the caller has just proved
        # they can name a channel, and confirming where it lives would answer a question
        # they were not entitled to ask.
        raise _refused(f"channel {channel_id} does not belong to guild {guild_id}")
    if not channel.postable:
        raise _refused(f"channel {channel_id} is not one Timothy can post in")


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
    discord: DiscordDep,
) -> NotificationChannelRead:
    """Point this guild's notifications at a channel it owns."""
    await get_guild(session, guild_id)
    await _owned_by(discord, guild_id=guild_id, channel_id=body.channel_id)

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
