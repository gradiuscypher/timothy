"""The adapter, and the one thing about it worth testing without a network.

discord.py's exceptions stop here. Everything above sees the domain's four errors, so
what matters is that each library failure arrives as the right one — retry and backoff
policy in phase 3 is written against that vocabulary and nothing else.

The client is a stand-in rather than a mock of discord.py's internals, which is exactly
the substitution ADR 0007's port exists to allow.
"""

from types import SimpleNamespace
from typing import Any

import discord
import pytest

from timothy_api.discord_adapter import DiscordAdapter, translate
from timothy_core.ports.discord import (
    DiscordError,
    DiscordPort,
    DiscordUnavailableError,
    ForbiddenError,
    GuildPermissions,
    NotFoundError,
    Notice,
    RateLimitedError,
)

GUILD = 1
USER = 2
CHANNEL = 3
ADMINISTRATOR = 1 << 3
NOTICE = Notice(title="Heads up", body="someone is listed", colour=0xFEE75C)


def response(status: int, *, retry_after: str | None = None) -> Any:  # noqa: ANN401
    """Enough of an `aiohttp` response for discord.py's exceptions to be constructed."""
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return SimpleNamespace(status=status, reason="", headers=headers)


def http_error(status: int, **kwargs: str) -> discord.HTTPException:
    return discord.HTTPException(response(status, **kwargs), {"code": 0, "message": "no"})


# -- translation -------------------------------------------------------------


def test_a_missing_thing_is_not_found() -> None:
    assert isinstance(translate(discord.NotFound(response(404), "gone")), NotFoundError)


def test_a_refusal_is_forbidden() -> None:
    assert isinstance(translate(discord.Forbidden(response(403), "no")), ForbiddenError)


def test_an_explicit_rate_limit_carries_its_delay() -> None:
    translated = translate(discord.RateLimited(4.5))

    assert isinstance(translated, RateLimitedError)
    assert translated.retry_after == 4.5


def test_a_429_carries_discords_own_delay() -> None:
    translated = translate(http_error(429, retry_after="2.5"))

    assert isinstance(translated, RateLimitedError)
    assert translated.retry_after == 2.5


def test_a_429_without_a_header_still_backs_off() -> None:
    translated = translate(http_error(429))

    assert isinstance(translated, RateLimitedError)
    assert translated.retry_after == 1.0


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_error_is_unavailable_and_so_retryable(status: int) -> None:
    assert isinstance(translate(http_error(status)), DiscordUnavailableError)


def test_a_client_error_is_neither_retryable_nor_special() -> None:
    translated = translate(http_error(400))

    assert type(translated) is DiscordError


def test_a_broken_connection_is_unavailable() -> None:
    """A transport failure and a 503 mean the same thing to a retry policy."""
    assert isinstance(translate(OSError("connection reset")), DiscordUnavailableError)
    assert isinstance(translate(TimeoutError()), DiscordUnavailableError)


def test_anything_else_is_still_a_discord_error() -> None:
    """Nothing from the library escapes as itself, however unfamiliar."""
    assert isinstance(translate(ValueError("surprise")), DiscordError)


# -- the adapter over a stand-in ---------------------------------------------


class StubGuild:
    def __init__(self, *, member: object | None = None, fails: Exception | None = None) -> None:
        self.member = member
        self.fails = fails
        self.banned: list[tuple[int, str]] = []
        self.unbanned: list[tuple[int, str]] = []

    async def ban(self, user: discord.Object, *, reason: str, **_: object) -> None:
        if self.fails is not None:
            raise self.fails
        self.banned.append((user.id, reason))

    async def unban(self, user: discord.Object, *, reason: str) -> None:
        if self.fails is not None:
            raise self.fails
        self.unbanned.append((user.id, reason))

    async def fetch_member(self, _member_id: int, /) -> object:
        if self.fails is not None:
            raise self.fails
        if self.member is None:
            raise discord.NotFound(response(404), "no member")
        return self.member


class StubChannel:
    def __init__(self, fails: Exception | None = None) -> None:
        self.fails = fails
        self.sent: list[discord.Embed] = []

    async def send(self, *, embed: discord.Embed) -> None:
        if self.fails is not None:
            raise self.fails
        self.sent.append(embed)


class StubContainer:
    """A channel that holds other channels — a category or a forum.

    It carries a guild like any guild channel, and nothing can be posted to it, which is
    the pair of facts `fetch_channel` reports.
    """

    def __init__(self, guild_id: int | None = GUILD) -> None:
        if guild_id is not None:
            self.guild = SimpleNamespace(id=guild_id)


class StubTextChannel(StubContainer, discord.abc.Messageable):
    """The same, but somewhere Timothy can actually say something.

    Inherits discord.py's own `Messageable` rather than being shaped to look like one:
    the adapter asks the library which of its channels can be sent to, so the stand-in
    has to answer that question the library's way or the test proves nothing.
    """


class StubClient:
    def __init__(
        self,
        guild: StubGuild | None = None,
        *,
        channel: StubChannel | None = None,
        login_fails: Exception | None = None,
        fetch_fails: Exception | None = None,
        lookup: object | Exception = None,
    ) -> None:
        self.guild = guild or StubGuild()
        self.channel = channel or StubChannel()
        self.login_fails = login_fails
        self.fetch_fails = fetch_fails
        self.lookup = lookup
        """What `fetch_channel` finds: a channel, or an exception to raise instead."""
        self.logins = 0
        self.closed = False

    async def login(self, _token: str, /) -> None:
        if self.login_fails is not None:
            raise self.login_fails
        self.logins += 1

    async def close(self) -> None:
        self.closed = True

    async def fetch_guild(self, _guild_id: int, /) -> Any:  # noqa: ANN401
        if self.fetch_fails is not None:
            raise self.fetch_fails
        return self.guild

    async def fetch_channel(self, _channel_id: int, /) -> Any:  # noqa: ANN401
        if isinstance(self.lookup, Exception):
            raise self.lookup
        return self.lookup

    def get_partial_messageable(self, _id: int, /) -> Any:  # noqa: ANN401
        return self.channel


def member(permissions: int = 0, *, role_ids: tuple[int, ...] = ()) -> object:
    """A discord.py member, shaped as much as the adapter reads.

    `roles` always leads with `@everyone`, whose ID is the guild's — that is Discord's
    own arrangement, and the reason the adapter drops it.
    """
    roles = [SimpleNamespace(id=GUILD), *(SimpleNamespace(id=role) for role in role_ids)]
    return SimpleNamespace(
        id=USER,
        display_name="someone",
        guild_permissions=SimpleNamespace(value=permissions),
        roles=roles,
    )


def adapter(client: StubClient) -> DiscordAdapter:
    return DiscordAdapter(client, "token")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_it_logs_in_once_and_only_when_used() -> None:
    """Not at startup: the container has to become healthy whether or not Discord is
    reachable."""
    client = StubClient()
    port = adapter(client)

    assert client.logins == 0

    await port.ban(guild_id=GUILD, user_id=USER, reason="listed")
    await port.ban(guild_id=GUILD, user_id=USER, reason="listed")

    assert client.logins == 1


@pytest.mark.anyio
async def test_a_bad_token_fails_the_call_not_the_process() -> None:
    port = adapter(StubClient(login_fails=discord.LoginFailure("bad token")))

    with pytest.raises(DiscordError):
        await port.ban(guild_id=GUILD, user_id=USER, reason="listed")


@pytest.mark.anyio
async def test_a_ban_reaches_discord_with_its_reason() -> None:
    client = StubClient()

    await adapter(client).ban(guild_id=GUILD, user_id=USER, reason="listed in spam")

    assert client.guild.banned == [(USER, "listed in spam")]


@pytest.mark.anyio
async def test_a_refused_ban_is_forbidden() -> None:
    client = StubClient(StubGuild(fails=discord.Forbidden(response(403), "no")))

    with pytest.raises(ForbiddenError):
        await adapter(client).ban(guild_id=GUILD, user_id=USER, reason="listed")


@pytest.mark.anyio
async def test_an_unban_reaches_discord() -> None:
    client = StubClient()

    await adapter(client).unban(guild_id=GUILD, user_id=USER, reason="reverted")

    assert client.guild.unbanned == [(USER, "reverted")]


@pytest.mark.anyio
async def test_a_present_member_comes_back() -> None:
    client = StubClient(StubGuild(member=member()))

    found = await adapter(client).fetch_member(guild_id=GUILD, user_id=USER)

    assert found is not None
    assert found.display_name == "someone"


@pytest.mark.anyio
async def test_a_members_roles_come_back_without_everyone() -> None:
    """`@everyone` has the guild's own ID, so carrying it would make a
    `POOL_MANAGER_ROLE_IDS` that named the management guild admit the whole guild."""
    role = 900_000_000_000_000_001
    client = StubClient(StubGuild(member=member(role_ids=(role,))))

    found = await adapter(client).fetch_member(guild_id=GUILD, user_id=USER)

    assert found is not None
    assert found.role_ids == frozenset({role})


@pytest.mark.anyio
async def test_an_absent_member_is_an_answer_not_an_error() -> None:
    """Enforcement is reactive (ADR 0004), so "not here" is the most common result there
    is."""
    assert await adapter(StubClient()).fetch_member(guild_id=GUILD, user_id=USER) is None


@pytest.mark.anyio
async def test_a_guild_that_is_gone_is_not_found() -> None:
    client = StubClient(fetch_fails=discord.NotFound(response(404), "gone"))

    with pytest.raises(NotFoundError):
        await adapter(client).fetch_member(guild_id=GUILD, user_id=USER)


@pytest.mark.anyio
async def test_permissions_come_back_resolved() -> None:
    client = StubClient(StubGuild(member=member(ADMINISTRATOR)))

    permissions = await adapter(client).guild_permissions(guild_id=GUILD, user_id=USER)

    assert permissions == GuildPermissions.administrator_only()
    assert permissions.administrator


@pytest.mark.anyio
async def test_a_non_member_has_no_permissions() -> None:
    """One shape for the deny path: no permissions means no."""
    permissions = await adapter(StubClient()).guild_permissions(guild_id=GUILD, user_id=USER)

    assert permissions == GuildPermissions.none()


@pytest.mark.anyio
async def test_a_refused_unban_is_translated_too() -> None:
    client = StubClient(StubGuild(fails=http_error(503)))

    with pytest.raises(DiscordUnavailableError):
        await adapter(client).unban(guild_id=GUILD, user_id=USER, reason="reverted")


@pytest.mark.anyio
async def test_a_failed_member_lookup_is_not_an_absence() -> None:
    """ "Discord did not answer" and "they are not here" must not collapse into the same
    result: one is retryable and the other is the end of the matter."""
    client = StubClient(StubGuild(fails=http_error(503)))

    with pytest.raises(DiscordUnavailableError):
        await adapter(client).fetch_member(guild_id=GUILD, user_id=USER)


@pytest.mark.anyio
async def test_a_failed_permission_lookup_is_not_a_denial() -> None:
    client = StubClient(StubGuild(fails=http_error(503)))

    with pytest.raises(DiscordUnavailableError):
        await adapter(client).guild_permissions(guild_id=GUILD, user_id=USER)


@pytest.mark.anyio
async def test_a_message_is_posted_without_fetching_the_channel_first() -> None:
    client = StubClient()

    await adapter(client).post_message(channel_id=CHANNEL, notice=NOTICE)

    [embed] = client.channel.sent
    assert embed.title == "Heads up"
    assert embed.description == "someone is listed"
    assert embed.colour == discord.Colour(0xFEE75C)


@pytest.mark.anyio
async def test_a_channel_that_refuses_is_forbidden() -> None:
    client = StubClient(channel=StubChannel(discord.Forbidden(response(403), "no")))

    with pytest.raises(ForbiddenError):
        await adapter(client).post_message(channel_id=CHANNEL, notice=NOTICE)


@pytest.mark.anyio
async def test_closing_before_use_touches_nothing() -> None:
    client = StubClient()

    await adapter(client).close()

    assert not client.closed


@pytest.mark.anyio
async def test_closing_after_use_releases_the_session() -> None:
    client = StubClient()
    port = adapter(client)
    await port.ban(guild_id=GUILD, user_id=USER, reason="listed")

    await port.close()

    assert client.closed


# -- fetching a channel ------------------------------------------------------


@pytest.mark.anyio
async def test_a_channel_reports_the_guild_that_owns_it() -> None:
    """The whole point: a notification channel may only be one its own guild owns."""
    channel = await adapter(StubClient(lookup=StubTextChannel())).fetch_channel(
        channel_id=CHANNEL
    )

    assert channel is not None
    assert channel.guild_id == GUILD
    assert channel.postable is True


@pytest.mark.anyio
async def test_a_dm_belongs_to_no_guild() -> None:
    """No `guild` attribute at all, so no guild may nominate it."""
    channel = await adapter(StubClient(lookup=StubTextChannel(None))).fetch_channel(
        channel_id=CHANNEL
    )

    assert channel is not None
    assert channel.guild_id is None


@pytest.mark.anyio
async def test_a_container_is_owned_but_not_postable() -> None:
    """A category belongs to the guild and is still not a place to say something, so the
    two facts have to be reported separately."""
    channel = await adapter(StubClient(lookup=StubContainer())).fetch_channel(
        channel_id=CHANNEL
    )

    assert channel is not None
    assert channel.guild_id == GUILD
    assert channel.postable is False


@pytest.mark.anyio
async def test_a_channel_that_is_gone_is_absent_not_an_error() -> None:
    client = StubClient(lookup=discord.NotFound(response(404), "gone"))

    assert await adapter(client).fetch_channel(channel_id=CHANNEL) is None


@pytest.mark.anyio
async def test_a_channel_timothy_cannot_see_is_also_absent() -> None:
    """For this question the two are the same: a channel Timothy cannot read is one it
    could not have been given."""
    client = StubClient(lookup=discord.Forbidden(response(403), "no"))

    assert await adapter(client).fetch_channel(channel_id=CHANNEL) is None


@pytest.mark.anyio
async def test_discord_being_down_still_raises() -> None:
    """Absence is an answer; unavailability is not, and must not read as "no channel"."""
    with pytest.raises(DiscordUnavailableError):
        await adapter(StubClient(lookup=http_error(503))).fetch_channel(channel_id=CHANNEL)


def test_the_real_client_satisfies_the_adapter() -> None:
    """The stand-in is only worth anything if the real thing fits the same shape."""
    assert isinstance(DiscordAdapter.create("token")._client, discord.Client)  # noqa: SLF001


def test_the_adapter_is_a_discord_port() -> None:
    """Structurally typed against the protocol rather than inheriting from it, the same
    way the fake is. The type checker reads this line."""
    port: DiscordPort = DiscordAdapter.create("token")

    assert port is not None
