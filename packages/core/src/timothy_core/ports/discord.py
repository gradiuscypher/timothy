"""Everything Timothy needs from Discord, and nothing else.

Six operations, deliberately (ADR 0007). Banning real people is not meaningfully
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
"""Discord's `ADMINISTRATOR` permission bit. The only permission Timothy's authorization
reads (ADR 0001), though the whole bitfield is carried. Authority over pools is a role
rather than a permission, and is read from :attr:`Member.role_ids` (ADR 0012)."""


@dataclass(frozen=True, slots=True)
class Member:
    """A user who is currently in a guild."""

    guild_id: int
    user_id: int
    display_name: str
    role_ids: frozenset[int] = frozenset()
    """Every role the member holds here, by ID.

    Raw, unlike :class:`GuildPermissions` beside it, because the question asked of it is
    "do they hold *this* role" (ADR 0012) rather than "what may they do", and Discord has
    nothing to resolve for that. The `@everyone` role is Discord's own bookkeeping and is
    not included: it would make every member of the guild hold a configured role whose ID
    happened to be the guild's.
    """


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


@dataclass(frozen=True, slots=True)
class Notice:
    """One thing Timothy posts to a guild's notification channel.

    Everything Timothy says in a channel is an embed — a title, a body and a colour a
    moderator can read at a glance without reading the words. The colour is a plain
    integer rather than a `discord.Colour` because the domain decides what a message
    *means*, and the adapter is the only place that knows how Discord spells it.
    """

    title: str
    body: str
    colour: int


@dataclass(frozen=True, slots=True)
class Channel:
    """A channel Timothy has been pointed at, and the two facts that decide if it may be.

    Both are asked once, when a guild's notification channel is configured, and never
    again: a channel ID is bound to its guild for the channel's whole life, so the
    relationship cannot drift under a stored row the way a permission can.
    """

    channel_id: int

    guild_id: int | None
    """Which guild owns it. `None` for a DM, which no guild owns and no guild may
    nominate."""

    postable: bool
    """Whether a notice can be sent here at all — false for a category or a forum, which
    are containers rather than places to say something."""


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

    async def fetch_channel(self, *, channel_id: int) -> Channel | None:
        """Look a channel up, or `None` if Timothy cannot see one by that ID.

        Absence is an ordinary answer rather than an error, as it is for
        :meth:`fetch_member`: a moderator pasting a channel ID gets it wrong more often
        than Discord breaks, and "no such channel" is a thing to tell them rather than a
        failure to retry.

        Asked when a notification channel is configured, so that a guild cannot nominate
        a channel belonging to some other guild. Deliberately *not* asked again at send
        time: the answer cannot change, and the send path already costs one Discord call
        per listed user per guild.

        Raises:
            RateLimitedError: back off and retry.
            DiscordUnavailableError: transport or 5xx failure.
        """
        ...

    async def post_message(self, *, channel_id: int, notice: Notice) -> None:
        """Post a notice to a channel — how a warn-level match reaches a guild.

        Raises:
            NotFoundError: the channel is gone.
            ForbiddenError: Timothy cannot post there.
            RateLimitedError: back off and retry.
            DiscordUnavailableError: transport or 5xx failure.
        """
        ...
