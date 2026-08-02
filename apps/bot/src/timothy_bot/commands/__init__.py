"""Timothy's slash commands, and where each of them is registered.

Two sets, split as they have always been. The global set is what a subscribing guild's
administrators use to configure their own guild: subscriptions, exceptions, the
notification channel, and reading the list of pools. The management set is pool and
listing ownership, and it exists only in the management guild — a second line of defence
in front of the backend's own check, which resolves `ADMINISTRATOR` in that guild against
Discord itself (ADR 0001).

`list_pools` is in both, as it is today: the management guild gets its own copy, which
shadows the global one there. Registering it twice is how it has always been shipped and
costs nothing to keep.

This replaces `tools/slash_cli` and its `json_commands/` tree. The command surface is now
declared where it is implemented, and `apps/bot/tests/command_surface.json` pins the
payloads against what that tool used to upload.
"""

import logging

import discord
from discord import app_commands

from timothy_bot.commands import exceptions, listings, notifications, pools, subscriptions

log = logging.getLogger(__name__)


def global_commands() -> list[app_commands.Command]:
    """What every guild Timothy is in gets."""
    return [
        exceptions.add_exception,
        notifications.add_notification,
        subscriptions.add_subscription,
        exceptions.delete_exception,
        notifications.delete_notification,
        subscriptions.delete_subscription,
        exceptions.list_exceptions,
        notifications.list_notification,
        pools.list_pools,
        subscriptions.list_subscriptions,
    ]


def management_commands() -> list[app_commands.Command]:
    """What only the management guild gets: pools and listings."""
    return [
        listings.add_ban,
        pools.add_pool,
        listings.delete_ban,
        pools.delete_pool,
        listings.get_user_bans,
        pools.list_pools,
    ]


def install(tree: app_commands.CommandTree, *, management_guild_id: int) -> None:
    """Put both sets on the tree.

    A management guild of zero installs the global set alone. That is the same failure
    the backend has already decided how to treat — nobody is an administrator of guild 0
    — so an unconfigured deployment ends up with pool management that exists nowhere
    rather than pool management that is open to anyone.
    """
    for command in global_commands():
        tree.add_command(command)

    if not management_guild_id:
        log.warning("no management guild configured — pool commands are not registered")
        return

    guild = discord.Object(id=management_guild_id)
    for command in management_commands():
        tree.add_command(command, guild=guild)
