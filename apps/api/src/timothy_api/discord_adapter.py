"""The production :class:`~timothy_core.ports.discord.DiscordPort`, over discord.py.

The backend is the only Discord client (ADR 0003), and it never joins the gateway: it
logs in for a REST session and nothing more. The gateway connection belongs to the bot
container, which makes no Discord writes at all.

This module is the one place discord.py's exceptions exist. Everything above it sees the
domain's four errors, so retry and backoff policy is written once against a vocabulary
that does not change when the library does — which is the whole of ADR 0007's first half.
Translation is a pure function for exactly that reason: it is the part worth testing, and
it is testable without a network.
"""

import asyncio
from http import HTTPStatus
from typing import Protocol, cast

import discord

from timothy_core.ports.discord import (
    DiscordError,
    DiscordUnavailableError,
    ForbiddenError,
    GuildPermissions,
    Member,
    NotFoundError,
    RateLimitedError,
)


def translate(error: Exception) -> DiscordError:
    """Restate a discord.py or transport failure in the domain's vocabulary.

    Order matters. `discord.NotFound` and `discord.Forbidden` are both
    `discord.HTTPException`, and a 429 or a 5xx arrives as the base class, so the
    specific cases come first and the status code decides the rest.
    """
    if isinstance(error, discord.RateLimited):
        return RateLimitedError(error.retry_after)
    if isinstance(error, discord.NotFound):
        return NotFoundError(str(error))
    if isinstance(error, discord.Forbidden):
        return ForbiddenError(str(error))
    if isinstance(error, discord.HTTPException):
        return _by_status(error)
    if isinstance(error, OSError | TimeoutError):
        return DiscordUnavailableError(str(error))
    return DiscordError(str(error))


def _by_status(error: discord.HTTPException) -> DiscordError:
    """What a plain HTTP failure means: back off, wait it out, or give up."""
    if error.status == HTTPStatus.TOO_MANY_REQUESTS:
        return RateLimitedError(_retry_after(error))
    if error.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return DiscordUnavailableError(str(error))
    return DiscordError(str(error))


def _retry_after(error: discord.HTTPException) -> float:
    """Discord's own `retry_after`, or a second if the body did not carry one."""
    body = error.response.headers.get("Retry-After") if error.response is not None else None
    try:
        return float(body) if body is not None else 1.0
    except ValueError:  # pragma: no cover — Discord sends a number or nothing
        return 1.0


class RestClient(Protocol):
    """The slice of `discord.Client` this adapter uses.

    Named so the adapter can be exercised against a stand-in. The alternative is mocking
    discord.py's internals, which is precisely what the port exists to avoid.
    """

    async def fetch_guild(self, guild_id: int, /) -> discord.Guild:
        """Fetch a guild, with its roles, so permissions can be resolved from it."""
        ...

    def get_partial_messageable(self, id: int, /) -> discord.PartialMessageable:  # noqa: A002
        """A channel handle that costs no round trip."""
        ...

    async def login(self, token: str, /) -> None:
        """Open a REST session. Never joins the gateway."""
        ...

    async def close(self) -> None:
        """Release the REST session."""
        ...


class DiscordAdapter:
    """Timothy's five Discord operations, and nothing else."""

    def __init__(self, client: RestClient, token: str) -> None:
        """Wrap a client. Nothing reaches Discord until the first call."""
        self._client = client
        self._token = token
        self._login_lock = asyncio.Lock()
        self._logged_in = False

    @classmethod
    def create(cls, token: str) -> "DiscordAdapter":
        """Build an adapter over a real REST-only client."""
        client = discord.Client(intents=discord.Intents.none())
        return cls(cast("RestClient", client), token)

    async def _ready(self) -> RestClient:
        """Log in on first use.

        Not at startup: the container has to become healthy whether or not Discord is
        reachable, and a token that is wrong should fail the request that needs it rather
        than the whole process.
        """
        if self._logged_in:
            return self._client
        async with self._login_lock:
            if not self._logged_in:
                try:
                    await self._client.login(self._token)
                except Exception as error:
                    raise translate(error) from error
                self._logged_in = True
        return self._client

    async def close(self) -> None:
        """Release the HTTP session."""
        if self._logged_in:
            await self._client.close()
            self._logged_in = False

    async def _guild(self, guild_id: int) -> discord.Guild:
        client = await self._ready()
        try:
            return await client.fetch_guild(guild_id)
        except Exception as error:
            raise translate(error) from error

    async def ban(self, *, guild_id: int, user_id: int, reason: str) -> None:
        """Ban a user from a guild."""
        guild = await self._guild(guild_id)
        try:
            await guild.ban(discord.Object(id=user_id), reason=reason, delete_message_seconds=0)
        except Exception as error:
            raise translate(error) from error

    async def unban(self, *, guild_id: int, user_id: int, reason: str) -> None:
        """Lift a ban."""
        guild = await self._guild(guild_id)
        try:
            await guild.unban(discord.Object(id=user_id), reason=reason)
        except Exception as error:
            raise translate(error) from error

    async def fetch_member(self, *, guild_id: int, user_id: int) -> Member | None:
        """Look a member up. Absence is an answer, not an error."""
        guild = await self._guild(guild_id)
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return None
        except Exception as error:
            raise translate(error) from error
        return Member(
            guild_id=guild_id,
            user_id=member.id,
            display_name=member.display_name,
            # `member.roles` leads with `@everyone`, whose ID is the guild's. Carrying it
            # would make a `POOL_MANAGER_ROLE_IDS` that named the guild admit everybody.
            role_ids=frozenset(role.id for role in member.roles if role.id != guild_id),
        )

    async def guild_permissions(self, *, guild_id: int, user_id: int) -> GuildPermissions:
        """Resolve a member's permissions, roles and ownership already folded in.

        A non-member has none, so authorization has a single shape: no permissions
        means no.
        """
        guild = await self._guild(guild_id)
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return GuildPermissions.none()
        except Exception as error:
            raise translate(error) from error
        return GuildPermissions(value=member.guild_permissions.value)

    async def post_message(self, *, channel_id: int, content: str) -> None:
        """Post to a channel, without spending a call to look it up first."""
        client = await self._ready()
        channel = client.get_partial_messageable(channel_id)
        try:
            await channel.send(content)
        except Exception as error:
            raise translate(error) from error
