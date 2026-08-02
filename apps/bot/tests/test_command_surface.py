"""The command surface, pinned against what the retired uploader used to upload.

`command_surface.json` is `tools/slash_cli/json_commands/` from the old repository,
verbatim — the payloads Discord has been serving for years. Moderators' muscle memory is
in those names and options, so they are preserved exactly (CONTEXT.md), and this is what
says so rather than a promise in a document.

Only the keys the old definitions declared are compared. discord.py fills in others
(`type`, `nsfw`, `contexts`, `integration_types`) that the uploader left to Discord's
defaults, and comparing those would pin the library rather than the surface.
"""

import json
from pathlib import Path
from typing import Any

import discord
import pytest
from discord import app_commands

from timothy_bot import commands

EXPECTED: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parent / "command_surface.json").read_text(encoding="utf-8")
)

MANAGEMENT_GUILD = 100_000_000_000_000_001

TEXT_CHANNEL_TYPES = [
    discord.ChannelType.text.value,
    discord.ChannelType.news.value,
]


@pytest.fixture
def tree() -> app_commands.CommandTree[discord.Client]:
    """A real tree with both sets installed, so payloads can be rendered from it."""
    client = discord.Client(intents=discord.Intents.none())
    built: app_commands.CommandTree[discord.Client] = app_commands.CommandTree(client)
    commands.install(built, management_guild_id=MANAGEMENT_GUILD)
    return built


def payloads(
    tree: app_commands.CommandTree[discord.Client], scope: str
) -> dict[str, dict[str, Any]]:
    """What would be uploaded for one of the two sets."""
    listed = commands.global_commands() if scope == "global" else commands.management_commands()
    return {command.name: command.to_dict(tree) for command in listed}


def cases(scope: str) -> list[tuple[str, dict[str, Any]]]:
    return sorted(EXPECTED[scope].items())


@pytest.mark.parametrize("scope", ["global", "management"])
def test_the_two_sets_hold_exactly_the_commands_they_always_did(
    tree: app_commands.CommandTree[discord.Client], scope: str
) -> None:
    assert set(payloads(tree, scope)) == set(EXPECTED[scope])


def test_list_pools_is_registered_in_both_sets() -> None:
    """As it has been all along: the management guild gets its own copy, which shadows
    the global one there."""
    assert commands.pools.list_pools in commands.global_commands()
    assert commands.pools.list_pools in commands.management_commands()


@pytest.mark.parametrize(("name", "expected"), cases("global"))
def test_a_global_command_is_unchanged(
    tree: app_commands.CommandTree[discord.Client], name: str, expected: dict[str, Any]
) -> None:
    assert_matches(payloads(tree, "global")[name], expected)


@pytest.mark.parametrize(("name", "expected"), cases("management"))
def test_a_management_command_is_unchanged(
    tree: app_commands.CommandTree[discord.Client], name: str, expected: dict[str, Any]
) -> None:
    assert_matches(payloads(tree, "management")[name], expected)


def assert_matches(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    """Every key the old definition declared, still declared and still the same."""
    for key, value in expected.items():
        if key == "options":
            assert_options(actual["options"], value)
        else:
            assert actual[key] == value, f"{actual['name']}.{key}"
    if "options" not in expected:
        assert actual["options"] == []


def assert_options(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    """Order included: it is the order a moderator types them in."""
    assert [option["name"] for option in actual] == [option["name"] for option in expected]
    for option, wanted in zip(actual, expected, strict=True):
        for key, value in wanted.items():
            assert option[key] == value, f"option {option['name']}.{key}"


def test_the_notification_channel_option_now_only_offers_text_channels(
    tree: app_commands.CommandTree[discord.Client],
) -> None:
    """The one deliberate widening of the old payload, and it narrows what can be picked.

    The old option accepted any channel and the bot answered "Provided channel was not a
    text channel" afterwards. Discord's picker can rule that out up front, so it does,
    and the after-the-fact branch is gone. Announcement channels stay in: messages can be
    sent to them, and a guild that reports moderation there is not doing anything odd.
    """
    option = payloads(tree, "global")["add_notification"]["options"][0]

    assert option["type"] == discord.AppCommandOptionType.channel.value
    assert option["channel_types"] == TEXT_CHANNEL_TYPES


def test_every_command_is_guild_only_and_administrator_only(
    tree: app_commands.CommandTree[discord.Client],
) -> None:
    """A second line of defence, not the only one. The backend resolves the same
    authority against Discord itself and refuses regardless of what these say."""
    for scope in ("global", "management"):
        for name, payload in payloads(tree, scope).items():
            assert payload["dm_permission"] is False, name
            assert payload["default_member_permissions"] == 0, name


def test_an_unconfigured_management_guild_registers_no_pool_commands() -> None:
    """Nobody is an administrator of guild 0, so the commands would fail anyway. Not
    registering them says so before a moderator types one."""
    client = discord.Client(intents=discord.Intents.none())
    empty: app_commands.CommandTree[discord.Client] = app_commands.CommandTree(client)

    commands.install(empty, management_guild_id=0)

    assert {command.name for command in empty.get_commands()} == set(EXPECTED["global"])
    assert empty.get_commands(guild=discord.Object(id=MANAGEMENT_GUILD)) == []


def test_the_management_set_is_registered_in_the_management_guild(
    tree: app_commands.CommandTree[discord.Client],
) -> None:
    registered = tree.get_commands(guild=discord.Object(id=MANAGEMENT_GUILD))

    assert {command.name for command in registered} == set(EXPECTED["management"])
