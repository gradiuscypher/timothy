"""Exceptions: a guild's declaration that Timothy never bans a particular user there.

Guild-wide, never scoped to one pool (ADR 0006). Creating one is read as "from now on":
it does not lift a ban Timothy has already issued, because that is a revert and every
revert in Timothy is opt-in. The flow for "let this person back in" is the unban itself,
which the backend already turns into an exception.
"""

import discord
from discord import app_commands

from timothy_bot import embeds
from timothy_bot.api import ApiError
from timothy_bot.commands.base import NOT_A_USER_ID, api_for, guild_of, reply, snowflake

CREATE = "Create Exception"
DELETE = "Delete Exception"
LIST = "List Exceptions"


@app_commands.command(name="add_exception", description="Add a ban exception to your server")
@app_commands.describe(user_id="User ID")
@app_commands.guild_only()
@app_commands.default_permissions()
async def add_exception(interaction: discord.Interaction, user_id: str) -> None:
    """Vouch for a user in this guild."""
    excepted = snowflake(user_id)
    if excepted is None:
        await reply(
            interaction,
            embeds.failed(
                CREATE, f"Exception for {user_id} failed to create:\n\n{NOT_A_USER_ID}"
            ),
        )
        return

    try:
        await api_for(interaction).create_exception(
            guild_id=guild_of(interaction), user_id=excepted
        )
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(
                CREATE, f"Exception for {user_id} failed to create:\n\n{error.detail}"
            ),
        )
        return

    await reply(
        interaction,
        embeds.succeeded(CREATE, f"Exception for {excepted} has been created successfully"),
    )


@app_commands.command(
    name="delete_exception", description="Remove a ban exception to your server"
)
@app_commands.describe(user_id="User ID")
@app_commands.guild_only()
@app_commands.default_permissions()
async def delete_exception(interaction: discord.Interaction, user_id: str) -> None:
    """Withdraw the vouch. Enforcement looks at this user here again."""
    excepted = snowflake(user_id)
    if excepted is None:
        await reply(
            interaction,
            embeds.failed(
                DELETE, f"Exception for {user_id} failed to delete:\n\n{NOT_A_USER_ID}"
            ),
        )
        return

    try:
        await api_for(interaction).delete_exception(
            guild_id=guild_of(interaction), user_id=excepted
        )
    except ApiError as error:
        await reply(
            interaction,
            embeds.failed(
                DELETE, f"Exception for {user_id} failed to delete:\n\n{error.detail}"
            ),
        )
        return

    await reply(
        interaction,
        embeds.succeeded(DELETE, f"Exception for {excepted} has been deleted successfully"),
    )


@app_commands.command(name="list_exceptions", description="List your server's ban exceptions")
@app_commands.guild_only()
@app_commands.default_permissions()
async def list_exceptions(interaction: discord.Interaction) -> None:
    """Everyone this guild has vouched for, one ID per line."""
    try:
        exceptions = await api_for(interaction).list_exceptions(guild_of(interaction))
    except ApiError as error:
        await reply(
            interaction, embeds.failed(LIST, f"Unable to fetch exceptions.\n\n{error.detail}")
        )
        return

    await reply(
        interaction,
        embeds.succeeded(LIST, embeds.lines(row["user_id"] for row in exceptions)),
    )
