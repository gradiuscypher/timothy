"""Resolution against Discord, and the cache in front of it.

The cache is the reason every authorised action fits inside Discord's three-second
interaction deadline (ADR 0003), so what it does and does not remember is worth pinning.
"""

from datetime import timedelta

import pytest

from timothy_api.permissions import PermissionResolver, TtlCache
from timothy_core.ports.discord import DiscordUnavailableError, GuildPermissions
from timothy_core.ports.fake import FakeDiscord

from .conftest import GUILD, GUILD_ADMIN, MANAGEMENT_GUILD, MEMBER, OTHER_GUILD, OUTSIDER

TTL = timedelta(seconds=60)


class Clock:
    """A hand-wound monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_cache_forgets_after_the_ttl() -> None:
    clock = Clock()
    cache: TtlCache[str, int] = TtlCache(TTL, clock)
    cache.put("k", value=1)

    assert cache.get("k") == 1
    clock.advance(60)
    assert cache.get("k") is None


def test_a_cache_misses_on_a_key_it_never_saw() -> None:
    assert TtlCache[str, int](TTL, Clock()).get("absent") is None


@pytest.mark.anyio
async def test_an_administrator_resolves_once(discord: FakeDiscord) -> None:
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    assert await resolver.is_administrator(guild_id=GUILD, user_id=GUILD_ADMIN)
    assert await resolver.is_administrator(guild_id=GUILD, user_id=GUILD_ADMIN)

    assert len(discord.calls_of("guild_permissions")) == 1


@pytest.mark.anyio
async def test_a_stale_answer_is_asked_again(discord: FakeDiscord) -> None:
    clock = Clock()
    resolver = PermissionResolver(discord, ttl=TTL, clock=clock)

    await resolver.is_administrator(guild_id=GUILD, user_id=GUILD_ADMIN)
    clock.advance(61)
    await resolver.is_administrator(guild_id=GUILD, user_id=GUILD_ADMIN)

    assert len(discord.calls_of("guild_permissions")) == 2


@pytest.mark.anyio
async def test_a_member_without_administrator_is_not_one(discord: FakeDiscord) -> None:
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    assert not await resolver.is_administrator(guild_id=GUILD, user_id=MEMBER)


@pytest.mark.anyio
async def test_a_guild_timothy_is_not_in_confers_no_authority(discord: FakeDiscord) -> None:
    """Authority over a guild only means anything here if Timothy is there to act on
    it."""
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    assert not await resolver.is_administrator(guild_id=999, user_id=GUILD_ADMIN)


@pytest.mark.anyio
async def test_discord_being_down_is_not_a_denial(discord: FakeDiscord) -> None:
    """Failing closed here would look identical to "you are not an administrator", and
    the caller would go and check their roles. It has to surface as a failure."""
    discord.fail(
        "guild_permissions",
        guild_id=GUILD,
        user_id=GUILD_ADMIN,
        error=DiscordUnavailableError("down"),
    )
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    with pytest.raises(DiscordUnavailableError):
        await resolver.is_administrator(guild_id=GUILD, user_id=GUILD_ADMIN)


@pytest.mark.anyio
async def test_membership_stops_at_the_first_guild_that_has_them(
    discord: FakeDiscord,
) -> None:
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    assert await resolver.is_member_of_any(
        guild_ids=[MANAGEMENT_GUILD, GUILD, OTHER_GUILD], user_id=GUILD_ADMIN
    )
    assert len(discord.calls_of("fetch_member")) == 1


@pytest.mark.anyio
async def test_a_non_member_costs_one_scan_and_then_none(discord: FakeDiscord) -> None:
    """The negative answer is the expensive one, so it is the one worth remembering."""
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())
    guilds = [MANAGEMENT_GUILD, GUILD, OTHER_GUILD]

    assert not await resolver.is_member_of_any(guild_ids=guilds, user_id=OUTSIDER)
    assert not await resolver.is_member_of_any(guild_ids=guilds, user_id=OUTSIDER)

    assert len(discord.calls_of("fetch_member")) == len(guilds)


@pytest.mark.anyio
async def test_a_guild_that_has_gone_away_is_skipped(discord: FakeDiscord) -> None:
    """The `guilds` table can name a guild Timothy has since been removed from; that is
    a stale row, not a failed lookup."""
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    assert await resolver.is_member_of_any(guild_ids=[999, GUILD], user_id=MEMBER)


@pytest.mark.anyio
async def test_permissions_are_cached_per_guild_not_per_user(discord: FakeDiscord) -> None:
    """An administrator in one guild is nobody in another, and the cache must not
    conflate them."""
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    assert await resolver.is_administrator(guild_id=GUILD, user_id=GUILD_ADMIN)
    assert not await resolver.is_administrator(guild_id=MANAGEMENT_GUILD, user_id=GUILD_ADMIN)


@pytest.mark.anyio
async def test_a_non_member_resolves_to_no_permissions(discord: FakeDiscord) -> None:
    """The port's contract: absence is `none()`, so the deny path has one shape."""
    assert (
        await discord.guild_permissions(guild_id=GUILD, user_id=OUTSIDER)
        == GuildPermissions.none()
    )


@pytest.mark.anyio
async def test_a_narrow_miss_does_not_answer_a_wider_question(discord: FakeDiscord) -> None:
    """Browser callers are scanned against the guilds Discord named at login (ADR 0010),
    so "no" from one of them is only "no" about those guilds. Keying the cache on the
    user alone would let a browser's narrow miss refuse the bot's wide question."""
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    assert not await resolver.is_member_of_any(guild_ids=[OTHER_GUILD], user_id=MEMBER)
    assert await resolver.is_member_of_any(
        guild_ids=[MANAGEMENT_GUILD, GUILD, OTHER_GUILD], user_id=MEMBER
    )


@pytest.mark.anyio
async def test_the_order_of_a_scan_is_not_part_of_its_cache_key(discord: FakeDiscord) -> None:
    """`X-Timothy-From-Guild` reorders the scan and nothing else, so the two orders have
    to share one cache entry — otherwise the hint would double the Discord calls it was
    added to avoid."""
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    assert not await resolver.is_member_of_any(
        guild_ids=[MANAGEMENT_GUILD, GUILD], user_id=OUTSIDER
    )
    assert not await resolver.is_member_of_any(
        guild_ids=[GUILD, MANAGEMENT_GUILD], user_id=OUTSIDER
    )

    assert len(discord.calls_of("fetch_member")) == 2


@pytest.mark.anyio
async def test_the_scan_still_asks_in_the_order_it_was_given(discord: FakeDiscord) -> None:
    """The set is the cache key; the list is the itinerary. Iterating the set instead
    would quietly undo phase 5's fix for `/list_pools` timing out at 123 guilds."""
    resolver = PermissionResolver(discord, ttl=TTL, clock=Clock())

    assert await resolver.is_member_of_any(
        guild_ids=[GUILD, MANAGEMENT_GUILD, OTHER_GUILD], user_id=MEMBER
    )

    assert [call.guild_id for call in discord.calls_of("fetch_member")] == [GUILD]
