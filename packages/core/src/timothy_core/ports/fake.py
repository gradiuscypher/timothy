"""An in-memory Discord.

This is the thing the enforcement engine, the sweep and the authorization checks are
actually tested against (ADR 0007): no network, no `discord.py` internals to mock, and
full speed. It is only useful if it is honest about the ways Discord fails, so it can be
told to rate limit, to lose members, and to refuse individual calls while others in the
same fan-out succeed.

Everything it does is recorded in `calls`, in order, so a test can assert on what
Timothy *tried* as well as on where it ended up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from timothy_core.ports.discord import (
    DiscordError,
    GuildPermissions,
    Member,
    NotFoundError,
    RateLimitedError,
)

Operation = Literal["ban", "unban", "fetch_member", "guild_permissions", "post_message"]


@dataclass(frozen=True, slots=True)
class Call:
    """One attempt against the fake, whether or not it succeeded."""

    op: Operation
    guild_id: int | None = None
    user_id: int | None = None
    channel_id: int | None = None
    reason: str | None = None
    content: str | None = None


@dataclass(frozen=True, slots=True)
class PostedMessage:
    """A message that reached a channel."""

    channel_id: int
    content: str


@dataclass(slots=True)
class FakeGuild:
    """One guild's state."""

    guild_id: int
    members: dict[int, Member] = field(default_factory=dict)
    permissions: dict[int, GuildPermissions] = field(default_factory=dict)
    bans: dict[int, str] = field(default_factory=dict)
    """Banned user ID to the audit reason Timothy gave."""


class FakeDiscord:
    """A `DiscordPort` backed by dictionaries.

    Structurally typed against the protocol rather than inheriting from it; the test
    suite asserts the two stay compatible.
    """

    def __init__(self) -> None:
        """Start empty: no guilds, no failures, no rate limit."""
        self.guilds: dict[int, FakeGuild] = {}
        self.channels: dict[int, int] = {}
        """Channel ID to the guild it belongs to."""

        self.messages: list[PostedMessage] = []
        self.calls: list[Call] = []

        self._member_failures: dict[tuple[Operation, int, int], DiscordError] = {}
        self._channel_failures: dict[int, DiscordError] = {}
        self._call_budget: int | None = None
        self._retry_after: float = 1.0
        self._calls_made = 0

    # -- setup ---------------------------------------------------------------

    def add_guild(self, guild_id: int) -> FakeGuild:
        """Put Timothy in a guild."""
        guild = FakeGuild(guild_id=guild_id)
        self.guilds[guild_id] = guild
        return guild

    def add_member(
        self,
        guild_id: int,
        user_id: int,
        *,
        display_name: str = "member",
        permissions: GuildPermissions | None = None,
    ) -> Member:
        """Put a user in a guild, optionally with permissions."""
        member = Member(guild_id=guild_id, user_id=user_id, display_name=display_name)
        guild = self._guild(guild_id)
        guild.members[user_id] = member
        guild.permissions[user_id] = permissions or GuildPermissions.none()
        return member

    def add_channel(self, channel_id: int, guild_id: int) -> None:
        """Give a guild a channel Timothy can post in."""
        self.channels[channel_id] = guild_id

    # -- failure injection ---------------------------------------------------

    def fail(
        self,
        op: Operation,
        *,
        guild_id: int,
        user_id: int,
        error: DiscordError,
    ) -> None:
        """Make one member-scoped call fail, every time, until `clear_failures`.

        This is the partial-failure lever: fail the ban for one user in a fan-out and
        the rest still land, which is exactly the case enforcement outcomes exist to
        record.
        """
        self._member_failures[op, guild_id, user_id] = error

    def fail_message(self, channel_id: int, error: DiscordError) -> None:
        """Make posting to one channel fail."""
        self._channel_failures[channel_id] = error

    def clear_failures(self) -> None:
        """Forget every injected failure."""
        self._member_failures.clear()
        self._channel_failures.clear()

    def rate_limit_after(self, calls: int, *, retry_after: float = 1.0) -> None:
        """Allow `calls` more calls, then raise `RateLimitedError` until `reset_rate_limit`."""
        self._call_budget = self._calls_made + calls
        self._retry_after = retry_after

    def reset_rate_limit(self) -> None:
        """Stop rate limiting — the fake's equivalent of waiting out `retry_after`."""
        self._call_budget = None

    # -- the port ------------------------------------------------------------

    async def ban(self, *, guild_id: int, user_id: int, reason: str) -> None:
        """Ban a user, removing them from the guild as Discord does."""
        self.calls.append(Call("ban", guild_id=guild_id, user_id=user_id, reason=reason))
        self._spend_call()
        guild = self._guild(guild_id)
        self._raise_if_injected("ban", guild_id, user_id)
        guild.bans[user_id] = reason
        guild.members.pop(user_id, None)
        guild.permissions.pop(user_id, None)

    async def unban(self, *, guild_id: int, user_id: int, reason: str) -> None:
        """Lift a ban.

        Raises:
            NotFoundError: if the user was not banned here.
        """
        self.calls.append(Call("unban", guild_id=guild_id, user_id=user_id, reason=reason))
        self._spend_call()
        guild = self._guild(guild_id)
        self._raise_if_injected("unban", guild_id, user_id)
        if user_id not in guild.bans:
            msg = f"user {user_id} is not banned in guild {guild_id}"
            raise NotFoundError(msg)
        del guild.bans[user_id]

    async def fetch_member(self, *, guild_id: int, user_id: int) -> Member | None:
        """Look a member up. `None` when they are not in the guild."""
        self.calls.append(Call("fetch_member", guild_id=guild_id, user_id=user_id))
        self._spend_call()
        guild = self._guild(guild_id)
        self._raise_if_injected("fetch_member", guild_id, user_id)
        return guild.members.get(user_id)

    async def guild_permissions(self, *, guild_id: int, user_id: int) -> GuildPermissions:
        """Resolve permissions. A non-member has none."""
        self.calls.append(Call("guild_permissions", guild_id=guild_id, user_id=user_id))
        self._spend_call()
        guild = self._guild(guild_id)
        self._raise_if_injected("guild_permissions", guild_id, user_id)
        return guild.permissions.get(user_id, GuildPermissions.none())

    async def post_message(self, *, channel_id: int, content: str) -> None:
        """Post to a channel.

        Raises:
            NotFoundError: if the channel is unknown.
        """
        self.calls.append(Call("post_message", channel_id=channel_id, content=content))
        self._spend_call()
        if channel_id not in self.channels:
            msg = f"no channel {channel_id}"
            raise NotFoundError(msg)
        injected = self._channel_failures.get(channel_id)
        if injected is not None:
            raise injected
        self.messages.append(PostedMessage(channel_id=channel_id, content=content))

    # -- assertions helpers --------------------------------------------------

    def is_banned(self, guild_id: int, user_id: int) -> bool:
        """Whether the fake currently holds a ban."""
        return user_id in self._guild(guild_id).bans

    def ban_reason(self, guild_id: int, user_id: int) -> str | None:
        """The reason recorded with a ban, if there is one."""
        return self._guild(guild_id).bans.get(user_id)

    def calls_of(self, op: Operation) -> list[Call]:
        """Every attempt at one operation, in order."""
        return [call for call in self.calls if call.op == op]

    # -- internals -----------------------------------------------------------

    def _guild(self, guild_id: int) -> FakeGuild:
        guild = self.guilds.get(guild_id)
        if guild is None:
            msg = f"no guild {guild_id}"
            raise NotFoundError(msg)
        return guild

    def _spend_call(self) -> None:
        self._calls_made += 1
        if self._call_budget is not None and self._calls_made > self._call_budget:
            raise RateLimitedError(self._retry_after)

    def _raise_if_injected(self, op: Operation, guild_id: int, user_id: int) -> None:
        injected = self._member_failures.get((op, guild_id, user_id))
        if injected is not None:
            raise injected
