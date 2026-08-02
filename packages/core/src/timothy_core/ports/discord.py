"""Everything Timothy needs from Discord, and nothing else.

Five operations, deliberately (ADR 0007). Banning real people is not meaningfully
reversible at the far end, so the surface that can do it is kept narrow enough to read
in one sitting, and narrow enough for a fake to implement honestly.

Errors are the domain's own. An adapter translates `discord.py`'s exceptions into these,
so retry and backoff policy can be written once against a stable vocabulary rather than
against whatever the library raises this month.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, Self

ADMINISTRATOR: Final = 1 << 3
"""Discord's `ADMINISTRATOR` permission bit. The only one Timothy's authorization
reads today (ADR 0001), though the whole bitfield is carried."""


@dataclass(frozen=True, slots=True)
class Member:
    """A user who is currently in a guild."""

    guild_id: int
    user_id: int
    display_name: str


@dataclass(frozen=True, slots=True)
class GuildPermissions:
    """A member's resolved permissions in one guild.

    Resolved, not raw: roles, overwrites and guild ownership are all folded in by the
    adapter, because Discord already knows how to do that and the domain should not
    learn.
    """

    value: int

    @property
    def administrator(self) -> bool:
        """Whether the member holds `ADMINISTRATOR`."""
        return bool(self.value & ADMINISTRATOR)

    @classmethod
    def none(cls) -> Self:
        """No permissions at all — what a non-member has."""
        return cls(value=0)

    @classmethod
    def administrator_only(cls) -> Self:
        """Convenience for tests and fixtures."""
        return cls(value=ADMINISTRATOR)


class DiscordError(Exception):
    """Something went wrong at Discord's end."""


class RateLimitedError(DiscordError):
    """Discord asked us to slow down.

    Attributes:
        retry_after: seconds Discord wants before the next attempt.
    """

    def __init__(self, retry_after: float) -> None:
        """Record how long Discord asked us to wait."""
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class NotFoundError(DiscordError):
    """The guild, channel or ban does not exist."""


class ForbiddenError(DiscordError):
    """Timothy lacks the permission, or the target outranks it.

    The everyday case is a guild that granted the bot no ban permission, or a listed
    user who happens to be a moderator there. Not retryable.
    """


class DiscordUnavailableError(DiscordError):
    """Discord is down or unreachable. Retryable."""


class DiscordPort(Protocol):
    """The only door between Timothy and Discord."""

    async def ban(self, *, guild_id: int, user_id: int, reason: str) -> None:
        """Ban a user from a guild.

        Idempotent: banning an already-banned user succeeds and refreshes the reason.

        Raises:
            NotFoundError: the guild is gone, or Timothy is no longer in it.
            ForbiddenError: no permission, or the target outranks Timothy.
            RateLimitedError: back off and retry.
            DiscordUnavailableError: transport or 5xx failure.
        """
        ...

    async def unban(self, *, guild_id: int, user_id: int, reason: str) -> None:
        """Lift a ban.

        Raises:
            NotFoundError: the guild is gone, or the user was not banned there.
            ForbiddenError: no permission.
            RateLimitedError: back off and retry.
            DiscordUnavailableError: transport or 5xx failure.
        """
        ...

    async def fetch_member(self, *, guild_id: int, user_id: int) -> Member | None:
        """Look a member up, or `None` if they are not in the guild.

        Absence is an ordinary answer, not an error: enforcement is reactive
        (ADR 0004), so "not here" is the most common result there is.

        Raises:
            NotFoundError: the guild is gone, or Timothy is no longer in it.
            RateLimitedError: back off and retry.
            DiscordUnavailableError: transport or 5xx failure.
        """
        ...

    async def guild_permissions(self, *, guild_id: int, user_id: int) -> GuildPermissions:
        """Resolve a member's permissions in a guild.

        A user who is not in the guild resolves to `GuildPermissions.none()` rather than
        raising, so the authorization path has one shape: no permissions means no.

        Raises:
            NotFoundError: the guild is gone, or Timothy is no longer in it.
            RateLimitedError: back off and retry.
            DiscordUnavailableError: transport or 5xx failure.
        """
        ...

    async def post_message(self, *, channel_id: int, content: str) -> None:
        """Post to a channel — how a warn-level match reaches a guild.

        Raises:
            NotFoundError: the channel is gone.
            ForbiddenError: Timothy cannot post there.
            RateLimitedError: back off and retry.
            DiscordUnavailableError: transport or 5xx failure.
        """
        ...
