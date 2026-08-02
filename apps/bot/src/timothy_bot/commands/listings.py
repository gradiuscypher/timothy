"""Listings, under the names moderators already know.

`/add_ban` creates a Listing. It bans nobody by itself: it is the assertion that a user
belongs on a pool, and enforcement is what follows from it in every guild subscribing at
ban level. The split between this surface and the domain's language is deliberate and is
not to be tidied away — see CONTEXT.md.

All three are management-guild commands: pools and listings are owned by administrators
of the one management guild (ADR 0001).
"""

import discord
from discord import app_commands

from timothy_bot import embeds
from timothy_bot.api import ApiError
from timothy_bot.commands.base import NOT_A_USER_ID, api_for, reply, snowflake

CREATE = "Create Ban"
DELETE = "Delete Ban"
LIST = "User Bans"


@app_commands.command(name="add_ban", description="Add a User ID to a banpool")
@app_commands.describe(
    user_id="User ID",
    pool_name="Banpool name",
    reason="Reason for adding the user to the banpool",
)
@app_commands.guild_only()
@app_commands.default_permissions()
async def add_ban(
    interaction: discord.Interaction, user_id: str, pool_name: str, reason: str
) -> None:
    """List a user on a pool, and let the enforcement it implies get under way."""
    failure = f"Failed to add `{user_id}` to {pool_name}."
    listed = snowflake(user_id)
    if listed is None:
        await reply(interaction, embeds.failed(CREATE, f"{failure}\n\n{NOT_A_USER_ID}"))
        return

    try:
        await api_for(interaction).create_listing(
            pool_name=pool_name, user_id=listed, reason=reason
        )
    except ApiError as error:
        await reply(interaction, embeds.failed(CREATE, f"{failure}\n\n{error.detail}"))
        return

    await reply(
        interaction,
        embeds.succeeded(
            CREATE,
            f"`{listed}` was added to `{pool_name}` successfully",
            fields=[
                ("Banpool Name", pool_name, True),
                # A mention, where the old bot spent a REST call to render
                # `name#discriminator`. Discriminators are gone — every one of them would
                # now read `#0` — and the bot makes no Discord calls of its own.
                ("User", f"<@{listed}>", True),
                ("Ban Reason", reason, False),
            ],
        ),
    )


@app_commands.command(name="delete_ban", description="Remove a User ID from a banpool")
@app_commands.describe(user_id="User ID", pool_name="Banpool name")
@app_commands.guild_only()
@app_commands.default_permissions()
async def delete_ban(interaction: discord.Interaction, user_id: str, pool_name: str) -> None:
    """Remove a listing.

    The bans it caused are left in place, which is what the API does by default and what
    the old bot did by having no other option. Lifting them is `?revert=true`, and giving
    that a slash command would make "tidy up a pool" and "unban everyone it touched" the
    same keystroke.
    """
    failure = f"Failed to remove `{user_id}` from {pool_name}."
    listed = snowflake(user_id)
    if listed is None:
        await reply(interaction, embeds.failed(DELETE, f"{failure}\n\n{NOT_A_USER_ID}"))
        return

    try:
        await api_for(interaction).delete_listing(pool_name=pool_name, user_id=listed)
    except ApiError as error:
        await reply(interaction, embeds.failed(DELETE, f"{failure}\n\n{error.detail}"))
        return

    await reply(
        interaction,
        embeds.succeeded(DELETE, f"`{listed}` was removed from `{pool_name}` successfully"),
    )


@app_commands.command(name="get_user_bans", description="Gets the bans for a specific User ID")
@app_commands.describe(user_id="User ID")
@app_commands.guild_only()
@app_commands.default_permissions()
async def get_user_bans(interaction: discord.Interaction, user_id: str) -> None:
    """Which pools list this user. One pool name per line, as before."""
    looked_up = snowflake(user_id)
    if looked_up is None:
        await reply(interaction, embeds.failed(LIST, NOT_A_USER_ID))
        return

    try:
        listings = await api_for(interaction).list_user_listings(looked_up)
    except ApiError:
        await reply(interaction, embeds.failed(LIST, "Unable to fetch bans for that user"))
        return

    await reply(
        interaction,
        embeds.succeeded(LIST, embeds.lines(listing["pool_name"] for listing in listings)),
    )
