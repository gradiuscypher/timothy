"""The gateway client: what it asks Discord for, and what it does with what arrives."""

import logging
from typing import Any, cast

import discord
import pytest
from discord import app_commands
from support import GUILD, LISTED_USER, Backend, FakeInteraction, is_red

from timothy_bot.api import Api
from timothy_bot.client import TimothyBot, intents, on_command_error
from timothy_bot.settings import Settings

MANAGEMENT_GUILD = 100_000_000_000_000_001


@pytest.fixture
def settings() -> Settings:
    return Settings(management_guild_id=MANAGEMENT_GUILD, sync_commands=False)


@pytest.fixture
def bot(api: Api, settings: Settings) -> TimothyBot:
    return TimothyBot(api, settings)


def test_the_intents_are_the_three_the_events_need() -> None:
    """`members` is privileged and has to be enabled in the developer portal. Without it
    Discord never sends `GUILD_MEMBER_ADD` and nobody is enforced at the door, while
    everything else keeps working — which is why it is asked for explicitly."""
    wanted = intents()

    assert wanted.guilds
    assert wanted.members
    assert wanted.moderation
    assert not wanted.message_content


@pytest.mark.anyio
async def test_setup_installs_both_command_sets(bot: TimothyBot) -> None:
    await bot.setup_hook()

    assert "list_pools" in {command.name for command in bot.tree.get_commands()}
    assert "add_ban" in {
        command.name
        for command in bot.tree.get_commands(guild=discord.Object(id=MANAGEMENT_GUILD))
    }


@pytest.mark.anyio
async def test_setup_can_be_told_not_to_upload(
    bot: TimothyBot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A staging instance run against the same application would otherwise overwrite the
    live command surface just by starting."""

    async def refuse(*_args: object, **_kwargs: object) -> list[app_commands.AppCommand]:
        message = "should not have synced"
        raise AssertionError(message)

    monkeypatch.setattr(app_commands.CommandTree, "sync", refuse)

    with caplog.at_level(logging.INFO, logger="timothy_bot.client"):
        await bot.setup_hook()

    assert "not syncing commands" in caplog.text


@pytest.mark.anyio
async def test_setup_uploads_both_scopes_when_asked(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registration lives in the bot now, retiring `slash_cli`: what exists and what is
    uploaded are the same list and cannot drift."""
    synced: list[int | None] = []

    async def record(
        _self: object, *, guild: discord.abc.Snowflake | None = None
    ) -> list[app_commands.AppCommand]:
        synced.append(guild.id if guild is not None else None)
        return []

    monkeypatch.setattr(app_commands.CommandTree, "sync", record)
    bot = TimothyBot(
        api,
        Settings(management_guild_id=MANAGEMENT_GUILD),
    )

    await bot.setup_hook()

    assert synced == [None, MANAGEMENT_GUILD]


@pytest.mark.anyio
async def test_connecting_announces_the_guilds(
    bot: TimothyBot, backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        TimothyBot, "guilds", property(lambda _self: [discord.Object(id=GUILD)])
    )

    await bot.on_ready()

    assert backend.called == ("PUT", f"/guilds/{GUILD}")


@pytest.mark.anyio
async def test_joining_a_guild_registers_it(bot: TimothyBot, backend: Backend) -> None:
    await bot.on_guild_join(cast("discord.Guild", discord.Object(id=GUILD)))

    assert backend.called == ("PUT", f"/guilds/{GUILD}")


@pytest.mark.anyio
async def test_leaving_a_guild_deregisters_it(bot: TimothyBot, backend: Backend) -> None:
    backend.replies(204)

    await bot.on_guild_remove(cast("discord.Guild", discord.Object(id=GUILD)))

    assert backend.called == ("DELETE", f"/guilds/{GUILD}")


@pytest.mark.anyio
async def test_a_member_joining_is_relayed(bot: TimothyBot, backend: Backend) -> None:
    backend.replies(202, {"action": "enforcement queued"})
    member = cast("Any", discord.Object(id=LISTED_USER))
    member.guild = discord.Object(id=GUILD)

    await bot.on_member_join(member)

    assert backend.called == ("POST", "/events/member-join")
    assert backend.sent == {"guild_id": str(GUILD), "user_id": str(LISTED_USER)}


@pytest.mark.anyio
async def test_an_unban_is_relayed(bot: TimothyBot, backend: Backend) -> None:
    backend.replies(202, {"action": "exception created"})

    await bot.on_member_unban(
        cast("discord.Guild", discord.Object(id=GUILD)),
        cast("discord.User", discord.Object(id=LISTED_USER)),
    )

    assert backend.called == ("POST", "/events/ban-remove")


@pytest.mark.anyio
async def test_an_unexpected_failure_still_answers_the_moderator(
    interaction: FakeInteraction,
) -> None:
    """Reaching here is a bug — the handlers turn everything they anticipate into an
    embed of their own — but an unanswered interaction tells a moderator nothing."""
    await on_command_error(
        cast("discord.Interaction", interaction),
        app_commands.AppCommandError("something nobody thought of"),
    )

    assert interaction.response.embed is not None
    assert is_red(interaction.response.embed)


@pytest.mark.anyio
async def test_a_failure_after_the_handler_answered_goes_to_the_followup(
    interaction: FakeInteraction,
) -> None:
    await interaction.response.send_message(embed=discord.Embed(title="already said"))

    await on_command_error(
        cast("discord.Interaction", interaction),
        app_commands.AppCommandError("and then it broke"),
    )

    assert interaction.followup.embed is not None


@pytest.mark.anyio
async def test_nothing_is_uploaded_to_a_guild_that_is_not_configured(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a management guild there is no second scope to sync, and no pool commands
    to put in it."""
    synced: list[int | None] = []

    async def record(
        _self: object, *, guild: discord.abc.Snowflake | None = None
    ) -> list[app_commands.AppCommand]:
        synced.append(guild.id if guild is not None else None)
        return []

    monkeypatch.setattr(app_commands.CommandTree, "sync", record)

    await TimothyBot(api, Settings(management_guild_id=0)).setup_hook()

    assert synced == [None]
