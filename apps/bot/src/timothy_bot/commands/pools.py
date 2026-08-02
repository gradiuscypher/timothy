"""Pools: creating them, deleting them, listing them.

`add_pool` and `delete_pool` are management-guild commands; `list_pools` is registered in
both sets, as it has always been. Renaming a pool has no command and will not get one —
it is web-only (PLAN.md), because the name is what every other command resolves by and a
rename typed in a hurry is a support ticket.
"""

import discord
from discord import app_commands

from timothy_bot import embeds
from timothy_bot.api import ApiError
from timothy_bot.commands.base import api_for, reply

CREATE = "Create Banpool"
DELETE = "Delete Banpool"
LIST = "List Banpools"

NO_DESCRIPTION = "no description"
"""Discord rejects an embed field with an empty value, and a pool's description is
optional now where Mongo's was always present."""


@app_commands.command(
    name="add_pool",
    description="Create a new banpool for adding users to and subscribing to.",
)
@app_commands.describe(pool_name="Banpool name", pool_desc="Banpool description")
@app_commands.guild_only()
@app_commands.default_permissions()
async def add_pool(interaction: discord.Interaction, pool_name: str, pool_desc: str) -> None:
    """Create a pool."""
    try:
        await api_for(interaction).create_pool(name=pool_name, description=pool_desc)
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(CREATE, f"Banpool `{pool_name}` failed to create.\n\n{error.detail}"),
        )
        return

    await reply(
        interaction, embeds.succeeded(CREATE, f"Banpool `{pool_name}` was created successfully")
    )


@app_commands.command(
    name="delete_pool", description="Delete a Banpool that was previously created."
)
@app_commands.describe(pool_name="Banpool name")
@app_commands.guild_only()
@app_commands.default_permissions()
async def delete_pool(interaction: discord.Interaction, pool_name: str) -> None:
    """Delete a pool, and every listing and subscription that hung off it.

    The bans it already caused stay. Lifting those is a revert, which only the API's
    `?revert=true` asks for and no slash command does — undoing a guild's bans is not
    something to do as a side effect of tidying up a pool (ADR 0005).
    """
    try:
        await api_for(interaction).delete_pool(pool_name)
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(DELETE, f"Banpool `{pool_name}` failed to delete.\n\n{error.detail}"),
        )
        return

    await reply(
        interaction, embeds.succeeded(DELETE, f"Banpool `{pool_name}` was deleted successfully")
    )


@app_commands.command(name="list_pools", description="List the existing Banpools")
@app_commands.guild_only()
@app_commands.default_permissions()
async def list_pools(interaction: discord.Interaction) -> None:
    """Every pool, one field each, exactly as before.

    The failure carries the backend's reason, where the old bot dropped it. This is the
    one command a member with no administrator anywhere can reach, so "not permitted" is
    the answer they most need and the one they were least likely to get.
    """
    try:
        pools = await api_for(interaction).list_pools()
    except ApiError as error:
        await reply(
            interaction, embeds.failed(LIST, f"Failed to list Banpools\n\n{error.detail}")
        )
        return

    await reply(
        interaction,
        embeds.succeeded(
            LIST,
            fields=[
                (pool["name"], pool["description"] or NO_DESCRIPTION, False) for pool in pools
            ],
        ),
    )
