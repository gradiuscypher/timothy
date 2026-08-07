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
    Channel,
    DiscordError,
    GuildPermissions,
    Member,
    NotFoundError,
    Notice,
    RateLimitedError,
    User,
)

Operation = Literal[
    "ban",
    "unban",
    "fetch_member",
    "fetch_user",
    "guild_permissions",
    "fetch_channel",
    "post_message",
]


@dataclass(frozen=True, slots=True)
class Call:
    """One attempt against the fake, whether or not it succeeded."""

    op: Operation
    guild_id: int | None = None
    user_id: int | None = None
    channel_id: int | None = None
    reason: str | None = None
    notice: Notice | None = None


@dataclass(frozen=True, slots=True)
class PostedMessage:
    """A notice that reached a channel."""

    channel_id: int
    notice: Notice

    @property
    def text(self) -> str:
        """Title and body together — what a test asking "did it say X" means."""
        return f"{self.notice.title}\n{self.notice.body}"


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
        self.channels: dict[int, Channel] = {}
        """Channel ID to the channel, which carries the guild that owns it."""

        self.users: dict[int, User] = {}
        """Accounts that exist at all, independent of any guild. An ID absent from here
        is one Discord has never heard of — a deleted account, or a typo carried in from
        a migration — which is a state the backfill has to handle honestly."""

        self.messages: list[PostedMessage] = []
        self.calls: list[Call] = []

        self._member_failures: dict[
            tuple[Operation, int, int], tuple[DiscordError, int | None]
        ] = {}
        """The injected failure and how many more calls it has left. `None` is forever."""

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
        role_ids: frozenset[int] | None = None,
    ) -> Member:
        """Put a user in a guild, optionally with permissions and roles."""
        member = Member(
            guild_id=guild_id,
            user_id=user_id,
            display_name=display_name,
            role_ids=role_ids or frozenset(),
        )
        guild = self._guild(guild_id)
        guild.members[user_id] = member
        guild.permissions[user_id] = permissions or GuildPermissions.none()
        return member

    def add_user(self, user_id: int, name: str) -> User:
        """Let an account exist, with a name, in no guild in particular."""
        user = User(user_id=user_id, name=name)
        self.users[user_id] = user
        return user

    def add_channel(
        self, channel_id: int, guild_id: int | None, *, postable: bool = True
    ) -> None:
        """Give a guild a channel Timothy can post in.

        `guild_id=None` is a DM, and `postable=False` a category or a forum: the two
        shapes a moderator can paste that no guild may nominate.
        """
        self.channels[channel_id] = Channel(
            channel_id=channel_id, guild_id=guild_id, postable=postable
        )

    # -- failure injection ---------------------------------------------------

    def fail(
        self,
        op: Operation,
        *,
        guild_id: int,
        user_id: int,
        error: DiscordError,
        times: int | None = None,
    ) -> None:
        """Make one member-scoped call fail, until `clear_failures` or `times` runs out.

        This is the partial-failure lever: fail the ban for one user in a fan-out and
        the rest still land, which is exactly the case enforcement outcomes exist to
        record.

        `times` is the *transient* lever, and it is a different question. A permanent
        failure is a guild that granted no ban permission — retrying collects the same
        refusal, and the honest answer is a `failed` outcome. A transient one is a 503
        from a load balancer, where retrying is the entire answer and the caller is only
        correct if it does. Without a failure that stops, a test cannot tell the two
        apart: everything that retries looks identical to everything that gives up.
        """
        self._member_failures[op, guild_id, user_id] = (error, times)

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

    async def fetch_user(self, *, user_id: int) -> User | None:
        """Look an account up. `None` when no such user exists.

        Names no guild, so there is no `_guild` lookup and no guild-scoped failure to
        inject: what can go wrong here is a rate limit, and that is the shared budget.
        """
        self.calls.append(Call("fetch_user", user_id=user_id))
        self._spend_call()
        return self.users.get(user_id)

    async def guild_permissions(self, *, guild_id: int, user_id: int) -> GuildPermissions:
        """Resolve permissions. A non-member has none."""
        self.calls.append(Call("guild_permissions", guild_id=guild_id, user_id=user_id))
        self._spend_call()
        guild = self._guild(guild_id)
        self._raise_if_injected("guild_permissions", guild_id, user_id)
        return guild.permissions.get(user_id, GuildPermissions.none())

    async def fetch_channel(self, *, channel_id: int) -> Channel | None:
        """Look a channel up. `None` when the fake has never heard of it."""
        self.calls.append(Call("fetch_channel", channel_id=channel_id))
        self._spend_call()
        return self.channels.get(channel_id)

    async def post_message(self, *, channel_id: int, notice: Notice) -> None:
        """Post a notice to a channel.

        Raises:
            NotFoundError: if the channel is unknown.
        """
        self.calls.append(Call("post_message", channel_id=channel_id, notice=notice))
        self._spend_call()
        if channel_id not in self.channels:
            msg = f"no channel {channel_id}"
            raise NotFoundError(msg)
        injected = self._channel_failures.get(channel_id)
        if injected is not None:
            raise injected
        self.messages.append(PostedMessage(channel_id=channel_id, notice=notice))

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
        if injected is None:
            return
        error, remaining = injected
        if remaining is None:
            raise error
        if remaining <= 1:
            del self._member_failures[op, guild_id, user_id]
        else:
            self._member_failures[op, guild_id, user_id] = (error, remaining - 1)
        raise error
