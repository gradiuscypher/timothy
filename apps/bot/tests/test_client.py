"""The gateway client: what it asks Discord for, and what it does with what arrives."""

import logging
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from discord import app_commands
from support import GUILD, LISTED_USER, Backend, FakeInteraction, is_red

from timothy_bot import commands
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


def _refused(status: int, code: int, text: str) -> discord.HTTPException:
    """What `tree.sync` raises when Discord will not take the commands."""
    response = SimpleNamespace(status=status, reason=text)
    return discord.HTTPException(response, {"code": code, "message": text})


@pytest.mark.anyio
async def test_a_refused_guild_sync_does_not_stop_the_bot(
    api: Api, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A management guild Timothy was never invited to answers `50001 Missing Access`.
    Dying here would trade a missing slash command for no enforcement at all — the
    gateway relay is the primary path (ADR 0004) — and the container would restart
    straight back into it, re-uploading the global commands every time."""

    async def refuse_the_guild(
        _self: object, *, guild: discord.abc.Snowflake | None = None
    ) -> list[app_commands.AppCommand]:
        if guild is not None:
            raise _refused(403, 50001, "Missing Access")
        return []

    monkeypatch.setattr(app_commands.CommandTree, "sync", refuse_the_guild)
    bot = TimothyBot(api, Settings(management_guild_id=MANAGEMENT_GUILD))

    with caplog.at_level(logging.ERROR):
        await bot.setup_hook()

    assert "TIMOTHY_MANAGEMENT_GUILD_ID" in caplog.text
    assert "applications.commands" in caplog.text


@pytest.mark.anyio
async def test_a_refused_global_sync_does_not_stop_the_bot_either(
    api: Api, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """And the guild sync is still attempted afterwards: the two are separate uploads and
    one failing says nothing about the other."""
    attempted: list[int | None] = []

    async def refuse_everything(
        _self: object, *, guild: discord.abc.Snowflake | None = None
    ) -> list[app_commands.AppCommand]:
        attempted.append(guild.id if guild is not None else None)
        raise _refused(500, 0, "Internal Server Error")

    monkeypatch.setattr(app_commands.CommandTree, "sync", refuse_everything)
    bot = TimothyBot(api, Settings(management_guild_id=MANAGEMENT_GUILD))

    with caplog.at_level(logging.ERROR):
        await bot.setup_hook()

    assert attempted == [None, MANAGEMENT_GUILD]
    assert "starting without them" in caplog.text


@pytest.mark.anyio
async def test_a_command_that_cannot_be_built_is_still_loud(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the upload is guarded. A tree that cannot be constructed is a bug in this
    repository, not somebody's Discord configuration."""

    def broken(*_args: object, **_kwargs: object) -> None:
        message = "two commands are named the same"
        raise RuntimeError(message)

    monkeypatch.setattr(commands, "install", broken)
    bot = TimothyBot(api, Settings(management_guild_id=MANAGEMENT_GUILD))

    with pytest.raises(RuntimeError, match="named the same"):
        await bot.setup_hook()


def a_guild(name: str, guild_id: int = GUILD) -> "discord.Guild":
    """A stand-in with the two attributes the client reads off a guild.

    `discord.Object` carries an id and nothing else, and the name is now half of what a
    registration says.
    """
    return cast("discord.Guild", SimpleNamespace(id=guild_id, name=name))


@pytest.mark.anyio
async def test_connecting_announces_the_guilds(
    bot: TimothyBot, backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(TimothyBot, "guilds", property(lambda _self: [a_guild("Neon Atrium")]))

    await bot.on_ready()

    assert backend.called == ("PUT", f"/guilds/{GUILD}")
    assert backend.sent == {"name": "Neon Atrium"}


@pytest.mark.anyio
async def test_joining_a_guild_registers_it(bot: TimothyBot, backend: Backend) -> None:
    await bot.on_guild_join(a_guild("Neon Atrium"))

    assert backend.called == ("PUT", f"/guilds/{GUILD}")
    assert backend.sent == {"name": "Neon Atrium"}


@pytest.mark.anyio
async def test_a_rename_is_relayed(bot: TimothyBot, backend: Backend) -> None:
    await bot.on_guild_update(a_guild("Neon Atrium"), a_guild("Neon Atrium Annexe"))

    assert backend.called == ("PUT", f"/guilds/{GUILD}")
    assert backend.sent == {"name": "Neon Atrium Annexe"}


@pytest.mark.anyio
async def test_a_guild_change_that_is_not_a_rename_is_not_relayed(
    bot: TimothyBot, backend: Backend
) -> None:
    """`GUILD_UPDATE` fires for a new banner too, and Timothy stores one field."""
    await bot.on_guild_update(a_guild("Neon Atrium"), a_guild("Neon Atrium"))

    assert backend.requests == []


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
