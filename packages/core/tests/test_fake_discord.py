"""The fake is only worth having if it fails the way Discord fails."""

import pytest

from timothy_core.ports.discord import (
    DiscordPort,
    DiscordUnavailableError,
    ForbiddenError,
    GuildPermissions,
    NotFoundError,
    Notice,
    RateLimitedError,
)
from timothy_core.ports.fake import FakeDiscord

pytestmark = pytest.mark.anyio

GUILD = 100
OTHER_GUILD = 200
CHANNEL = 300
USER = 400
NOTICE = Notice(title="Heads up", body="someone is listed", colour=0xFEE75C)


def test_the_fake_satisfies_the_port() -> None:
    """Type-checked, not asserted: `ty` fails the build if the two ever diverge."""
    port: DiscordPort = FakeDiscord()

    assert port is not None


@pytest.fixture
def discord() -> FakeDiscord:
    fake = FakeDiscord()
    fake.add_guild(GUILD)
    fake.add_channel(CHANNEL, GUILD)
    fake.add_member(GUILD, USER, display_name="nuisance")
    return fake


async def test_banning_removes_the_member_and_records_the_reason(discord: FakeDiscord) -> None:
    await discord.ban(guild_id=GUILD, user_id=USER, reason="Timothy: listed in global")

    assert discord.is_banned(GUILD, USER)
    assert discord.ban_reason(GUILD, USER) == "Timothy: listed in global"
    assert await discord.fetch_member(guild_id=GUILD, user_id=USER) is None


async def test_banning_twice_is_idempotent(discord: FakeDiscord) -> None:
    """A retry after an ambiguous failure must not be a second failure."""
    await discord.ban(guild_id=GUILD, user_id=USER, reason="first")
    await discord.ban(guild_id=GUILD, user_id=USER, reason="second")

    assert discord.ban_reason(GUILD, USER) == "second"


async def test_a_missing_member_is_an_answer_not_an_error(discord: FakeDiscord) -> None:
    assert await discord.fetch_member(guild_id=GUILD, user_id=999) is None


async def test_a_guild_timothy_is_not_in_raises(discord: FakeDiscord) -> None:
    with pytest.raises(NotFoundError):
        await discord.fetch_member(guild_id=OTHER_GUILD, user_id=USER)


async def test_unbanning_someone_who_is_not_banned_raises(discord: FakeDiscord) -> None:
    with pytest.raises(NotFoundError):
        await discord.unban(guild_id=GUILD, user_id=USER, reason="revert")


async def test_unbanning_lifts_the_ban(discord: FakeDiscord) -> None:
    await discord.ban(guild_id=GUILD, user_id=USER, reason="listed")
    await discord.unban(guild_id=GUILD, user_id=USER, reason="revert")

    assert not discord.is_banned(GUILD, USER)


async def test_permissions_resolve_and_default_closed(discord: FakeDiscord) -> None:
    discord.add_member(GUILD, 500, permissions=GuildPermissions.administrator_only())

    admin = await discord.guild_permissions(guild_id=GUILD, user_id=500)
    ordinary = await discord.guild_permissions(guild_id=GUILD, user_id=USER)
    stranger = await discord.guild_permissions(guild_id=GUILD, user_id=999)

    assert admin.administrator
    assert not ordinary.administrator
    assert stranger == GuildPermissions.none()


async def test_posting_needs_a_channel_that_exists(discord: FakeDiscord) -> None:
    await discord.post_message(channel_id=CHANNEL, notice=NOTICE)

    assert [message.notice for message in discord.messages] == [NOTICE]

    with pytest.raises(NotFoundError):
        await discord.post_message(channel_id=999, notice=NOTICE)


async def test_one_ban_can_fail_while_the_rest_of_the_fan_out_lands(
    discord: FakeDiscord,
) -> None:
    """The partial failure that enforcement outcomes exist to record."""
    for user_id in (1, 2, 3):
        discord.add_member(GUILD, user_id)
    discord.fail("ban", guild_id=GUILD, user_id=2, error=ForbiddenError("outranks Timothy"))

    banned, refused = [], []
    for user_id in (1, 2, 3):
        try:
            await discord.ban(guild_id=GUILD, user_id=user_id, reason="listed")
        except ForbiddenError:
            refused.append(user_id)
        else:
            banned.append(user_id)

    assert banned == [1, 3]
    assert refused == [2]
    assert not discord.is_banned(GUILD, 2)


async def test_a_rate_limit_stops_everything_and_says_how_long(discord: FakeDiscord) -> None:
    discord.rate_limit_after(2, retry_after=1.5)

    await discord.ban(guild_id=GUILD, user_id=1, reason="listed")
    await discord.ban(guild_id=GUILD, user_id=2, reason="listed")
    with pytest.raises(RateLimitedError) as limited:
        await discord.ban(guild_id=GUILD, user_id=3, reason="listed")

    assert limited.value.retry_after == 1.5
    assert not discord.is_banned(GUILD, 3)

    discord.reset_rate_limit()
    await discord.ban(guild_id=GUILD, user_id=3, reason="listed")
    assert discord.is_banned(GUILD, 3)


async def test_a_rate_limited_call_still_counts_as_an_attempt(discord: FakeDiscord) -> None:
    """A worker that retries needs to see what it already tried."""
    discord.rate_limit_after(0)

    with pytest.raises(RateLimitedError):
        await discord.ban(guild_id=GUILD, user_id=USER, reason="listed")

    assert [call.user_id for call in discord.calls_of("ban")] == [USER]


async def test_failures_can_be_cleared(discord: FakeDiscord) -> None:
    discord.fail("ban", guild_id=GUILD, user_id=USER, error=DiscordUnavailableError("503"))

    with pytest.raises(DiscordUnavailableError):
        await discord.ban(guild_id=GUILD, user_id=USER, reason="listed")

    discord.clear_failures()
    await discord.ban(guild_id=GUILD, user_id=USER, reason="listed")

    assert discord.is_banned(GUILD, USER)


async def test_a_failed_post_never_reaches_the_channel(discord: FakeDiscord) -> None:
    discord.fail_message(CHANNEL, ForbiddenError("cannot post here"))

    with pytest.raises(ForbiddenError):
        await discord.post_message(channel_id=CHANNEL, notice=NOTICE)

    assert discord.messages == []
    assert len(discord.calls_of("post_message")) == 1
