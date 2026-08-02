"""The notification channel: where Timothy reports what it did in a guild.

Warn-level matches land here, and a guild subscribed at warn with no channel set records
a `failed` outcome rather than a warned one — so setting this is not cosmetic.
"""

import discord
from discord import app_commands

from timothy_bot import embeds
from timothy_bot.api import ApiError
from timothy_bot.commands.base import api_for, guild_of, reply

CREATE = "Create Notification Channel"
DELETE = "Delete Notification Channel"
LIST = "List Notification Channel"


@app_commands.command(
    name="add_notification", description="Set the channel used for banpool notifications"
)
@app_commands.describe(channel_id="Channel to send notifications to")
@app_commands.guild_only()
@app_commands.default_permissions()
async def add_notification(
    interaction: discord.Interaction, channel_id: discord.TextChannel
) -> None:
    """Point this guild's notifications at a channel.

    The option asks for a text channel, so Discord's own picker will not offer a voice
    channel or a category. That retires the old bot's "Provided channel was not a text
    channel" branch, which existed because the option accepted anything and the check had
    to happen afterwards.
    """
    try:
        await api_for(interaction).set_notification_channel(
            guild_id=guild_of(interaction), channel_id=channel_id.id
        )
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(CREATE, f"Unable to set notification channel:\n\n{error.detail}"),
        )
        return

    await reply(
        interaction,
        embeds.succeeded(CREATE, f"Notification channel set to <#{channel_id.id}>"),
    )


@app_commands.command(
    name="delete_notification",
    description="Unset the currently set channel for banpool notifications",
)
@app_commands.guild_only()
@app_commands.default_permissions()
async def delete_notification(interaction: discord.Interaction) -> None:
    """Stop reporting to a channel."""
    try:
        await api_for(interaction).delete_notification_channel(guild_of(interaction))
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(DELETE, f"Unable to unset notification channel:\n\n{error.detail}"),
        )
        return

    await reply(interaction, embeds.succeeded(DELETE, "Notification channel unset"))


@app_commands.command(
    name="list_notification",
    description="List the currently set channel for banpool notifications",
)
@app_commands.guild_only()
@app_commands.default_permissions()
async def list_notification(interaction: discord.Interaction) -> None:
    """Where this guild's notifications currently go."""
    try:
        channel = await api_for(interaction).read_notification_channel(guild_of(interaction))
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(LIST, f"Error listing notification channel:\n\n{error.detail}"),
        )
        return

    await reply(
        interaction,
        embeds.succeeded(LIST, f"Current notification channel: <#{channel['channel_id']}>"),
    )
