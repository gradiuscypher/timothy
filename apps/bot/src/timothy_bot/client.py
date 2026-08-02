"""The gateway client itself.

Three responsibilities and no others: hold the connection, hand interactions to the
command handlers, and relay the two events the backend cares about. It makes no Discord
writes — the bans, the unbans and the notification messages are all the backend's, over
REST, with the same token (ADR 0003).

The intents are the smallest set that delivers those events. `members` is privileged and
has to be enabled for the application in Discord's developer portal; without it Discord
never sends `GUILD_MEMBER_ADD`, and "banned at the door" quietly stops happening while
everything else still works. That failure is silent by nature, so it is checked for and
logged on connect.
"""

import logging

import discord
from discord import app_commands

from timothy_bot import commands, embeds, relay
from timothy_bot.api import Api
from timothy_bot.settings import Settings

log = logging.getLogger(__name__)

UNEXPECTED = "Something went wrong"


def intents() -> discord.Intents:
    """Guilds for membership of the tree, members for joins, moderation for unbans."""
    wanted = discord.Intents.none()
    wanted.guilds = True
    wanted.members = True
    wanted.moderation = True
    return wanted


async def on_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    """Last resort for a handler that raised something it did not expect.

    The handlers turn every failure they anticipate into a red embed of their own, so
    reaching here means a bug. The moderator still gets an answer, because an interaction
    left unanswered shows them "the application did not respond" and tells them nothing.
    """
    log.error("command %s failed", interaction.command, exc_info=error)
    embed = embeds.failed(UNEXPECTED, str(error))
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed)
    else:
        await interaction.response.send_message(embed=embed)


class TimothyBot(discord.Client):
    """Timothy's half of the gateway."""

    def __init__(self, api: Api, settings: Settings) -> None:
        """Wire the client to a backend it will speak to as `system`."""
        super().__init__(intents=intents())
        self.api = api
        self.settings = settings
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Build the command tree, and upload it if asked to.

        Registration lives here now, retiring `slash_cli`: the commands that exist and
        the commands that are uploaded can no longer drift apart, because they are the
        same list. Uploading is still a setting, so a second instance run against the
        same application — a staging bot, a local session — cannot overwrite the live
        surface just by starting.
        """
        commands.install(self.tree, management_guild_id=self.settings.management_guild_id)
        self.tree.error(on_command_error)

        if not self.settings.sync_commands:
            log.info("not syncing commands: TIMOTHY_SYNC_COMMANDS is off")
            return

        uploaded = await self.tree.sync()
        log.info("synced %d global commands", len(uploaded))
        if self.settings.management_guild_id:
            guild = discord.Object(id=self.settings.management_guild_id)
            in_management = await self.tree.sync(guild=guild)
            log.info(
                "synced %d commands in the management guild %s",
                len(in_management),
                self.settings.management_guild_id,
            )

    async def on_ready(self) -> None:
        """Announce every guild Timothy is in, on connect and on every reconnect."""
        log.info("connected as %s in %d guilds", self.user, len(self.guilds))
        if not self.intents.members:  # pragma: no cover — set in `intents()`
            log.warning("the members intent is off: joins will not be enforced at the door")
        await relay.announce_guilds(self.api, [guild.id for guild in self.guilds])

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Timothy was added to a guild."""
        await relay.guild_joined(self.api, guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Timothy was removed from a guild."""
        await relay.guild_left(self.api, guild.id)

    async def on_member_join(self, member: discord.Member) -> None:
        """Someone joined a guild Timothy is in."""
        await relay.member_joined(self.api, guild_id=member.guild.id, user_id=member.id)

    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        """A ban was lifted somewhere Timothy is."""
        await relay.ban_removed(self.api, guild_id=guild.id, user_id=user.id)
