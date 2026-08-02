"""Subscriptions: a guild's decision to enforce a pool, and at what level.

Global commands, restricted to administrators of the guild they are used in — which the
backend also insists on, having resolved it against Discord rather than trusted the flag.
"""

import discord
from discord import app_commands

from timothy_bot import embeds
from timothy_bot.api import ApiError
from timothy_bot.commands.base import api_for, guild_of, reply

CREATE = "Create Subscription"
DELETE = "Delete Subscription"
LIST = "List Subscriptions"


@app_commands.command(
    name="add_subscription", description="Add a banpool subscription to your server"
)
@app_commands.describe(pool_name="Pool Name", subscription_level="Subscription Level")
@app_commands.choices(
    subscription_level=[
        app_commands.Choice(name="warn", value="warn"),
        app_commands.Choice(name="ban", value="ban"),
    ]
)
@app_commands.guild_only()
@app_commands.default_permissions()
async def add_subscription(
    interaction: discord.Interaction,
    pool_name: str,
    subscription_level: app_commands.Choice[str],
) -> None:
    """Subscribe to a pool, or move an existing subscription to another level."""
    level = subscription_level.value
    try:
        await api_for(interaction).set_subscription(
            guild_id=guild_of(interaction), pool_name=pool_name, level=level
        )
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(CREATE, f"Failed to subscribe to {pool_name}\n\n{error.detail}"),
        )
        return

    await reply(
        interaction,
        embeds.succeeded(
            CREATE, f"Subscription for {pool_name}:{level} has been created successfully"
        ),
    )


@app_commands.command(
    name="delete_subscription", description="Remove a banpool subscription from your server"
)
# "pool_name" is the description the original shipped with, typo and all. Moderators read
# it in the picker; changing it is a product decision, not a tidy-up.
@app_commands.describe(pool_name="pool_name")
@app_commands.guild_only()
@app_commands.default_permissions()
async def delete_subscription(interaction: discord.Interaction, pool_name: str) -> None:
    """Unsubscribe. The bans this pool already caused here stay.

    Which is what the success message has always said, and it stays true: reverting is
    `?revert=true` on the API, and no slash command asks for it.
    """
    try:
        await api_for(interaction).delete_subscription(
            guild_id=guild_of(interaction), pool_name=pool_name
        )
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(
                DELETE, f"Subscription to {pool_name} failed to delete:\n\n{error.detail}"
            ),
        )
        return

    await reply(
        interaction,
        embeds.succeeded(
            DELETE,
            f"Subscription for {pool_name} has been deleted successfully. Please note, "
            "this does not remove the bans already in place.",
        ),
    )


@app_commands.command(
    name="list_subscriptions", description="List your server's banpool subscriptions"
)
@app_commands.guild_only()
@app_commands.default_permissions()
async def list_subscriptions(interaction: discord.Interaction) -> None:
    """What this guild has subscribed to, `pool:level` per line.

    No fabricated `global:ban` line. The old bot printed one because `global` was a
    reserved name with no row behind it; it is an ordinary pool now, and a guild that has
    unsubscribed from it must be able to see that it has (ADR 0002).
    """
    try:
        subscriptions = await api_for(interaction).list_subscriptions(guild_of(interaction))
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(LIST, f"Unable to fetch subscriptions.\n\n{error.detail}"),
        )
        return

    await reply(
        interaction,
        embeds.succeeded(
            LIST,
            embeds.lines(
                f"{subscription['pool_name']}:{subscription['level']}"
                for subscription in subscriptions
            ),
        ),
    )
