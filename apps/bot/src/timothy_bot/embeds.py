"""How the bot answers in Discord.

The old bot replied to every command with an embed: a title naming the operation, a green
description when it worked and a red one carrying the failure when it did not. Moderators
have years of that shape in their eyes, so it is preserved here, and the wording of each
message lives with the command that sends it.

Two departures, both forced by the rest of the world rather than chosen:

* A failure now shows the backend's `detail`, which is a sentence a person can act on
  ("no such pool: spma"), where the old bot showed a database driver's error.
* An empty description is omitted rather than sent. Discord rejects an embed whose
  description is the empty string, and a guild with no exceptions is not an error.
"""

from collections.abc import Iterable

import discord

Field = tuple[str, str, bool]
"""A name, a value, and whether it sits inline — Serenity's tuple, kept."""


def succeeded(
    title: str, description: str | None = None, fields: Iterable[Field] = ()
) -> discord.Embed:
    """The green one."""
    return _embed(title, description, fields, discord.Colour.dark_green())


def failed(title: str, description: str) -> discord.Embed:
    """The red one."""
    return _embed(title, description, (), discord.Colour.red())


def lines(values: Iterable[str]) -> str | None:
    """One per line, or nothing at all when there are none."""
    body = "\n".join(values)
    return body or None


def _embed(
    title: str, description: str | None, fields: Iterable[Field], colour: discord.Colour
) -> discord.Embed:
    embed = discord.Embed(title=title, colour=colour)
    if description:
        embed.description = description
    for name, value, inline in fields:
        embed.add_field(name=name, value=value, inline=inline)
    return embed
