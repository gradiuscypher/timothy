"""What every command handler needs, and nothing more.

The three things a handler does before it does anything interesting: find the backend,
find the guild it was invoked in, and answer. Kept here so sixteen handlers can read as
sixteen sentences about the domain.
"""

from typing import Protocol, cast

import discord

from timothy_bot.api import Api

NO_GUILD = "a guild-only command arrived without a guild"

NOT_A_USER_ID = "that is not a user ID"
"""What a moderator is told when the `user_id` option is not a snowflake.

The option is a string, as it has always been — Discord's `USER` option type only offers
people the client can resolve, and half the point of a shared pool is listing someone who
is not in your guild. So the parsing is ours, and so is the failure.
"""


def snowflake(raw: str) -> int | None:
    """A Discord ID out of what someone typed, or `None` if that is not one."""
    text = raw.strip()
    return int(text) if text.isdigit() and text != "0" else None


class HasApi(Protocol):
    """The slice of the client a handler uses.

    A protocol rather than an import of the client, so the command modules and the client
    module do not have to know about each other, and a test can pass anything at all.
    """

    api: Api


def api_for(interaction: discord.Interaction) -> Api:
    """The backend, acting for the moderator who typed the command.

    Identity only. What that moderator may do is the backend's question, resolved against
    Discord itself — the `default_member_permissions` on the command is a second line of
    defence now, not the only one.

    The guild travels too, and is still not authority: it tells the backend which guild to
    check first when a permission needs a scan of all of them. `/list_pools` is the one
    that does, and at a hundred-odd guilds an unordered scan does not finish inside
    Discord's interaction deadline.
    """
    return cast("HasApi", interaction.client).api.as_user(
        interaction.user.id, from_guild=interaction.guild_id
    )


def guild_of(interaction: discord.Interaction) -> int:
    """The guild the command was used in.

    Every one of Timothy's commands is `guild_only`, so this is total in practice; the
    check is here because Discord's payload says `Optional` and a crash inside a handler
    is a silent failure to the moderator.
    """
    if interaction.guild_id is None:  # pragma: no cover — guild_only forbids it
        raise RuntimeError(NO_GUILD)
    return interaction.guild_id


async def reply(interaction: discord.Interaction, embed: discord.Embed) -> None:
    """Answer the interaction directly.

    Never deferred. The API answers inside Discord's three-second deadline for everything
    a command does, because a mutation enqueues its fan-out instead of performing it — so
    the bot can say "done, enforcement is under way" immediately, and truthfully.
    """
    await interaction.response.send_message(embed=embed)
