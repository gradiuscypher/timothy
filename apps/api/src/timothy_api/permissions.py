"""Resolving a caller's authority against Discord, with a short-lived cache.

Every authorised action costs a permission lookup inside Discord's three-second
interaction deadline (ADR 0003), so the answers are cached for `PERMISSION_CACHE_TTL`.
The cache is per process and in memory: it is a rate-limit shield, not a store, and
losing it on restart costs one extra call.

Three questions get asked, and they do not all take the same call. Whether someone is an
administrator is `guild_permissions`, which answers `none()` for a non-member and so
cannot tell "not here" from "here with nothing". Whether someone is a member at all, and
which roles they hold, are both `fetch_member`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from timothy_core.ports.discord import NotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import timedelta

    from timothy_core.ports.discord import DiscordPort


@dataclass(frozen=True, slots=True)
class _Cached[T]:
    value: T
    expires_at: float


class TtlCache[K, V]:
    """A dictionary whose entries stop being true after a while.

    Expired entries are dropped when they are next looked up rather than swept: the key
    space is (guild, user) pairs seen in the last minute, which stays small on its own.
    """

    def __init__(self, ttl: timedelta, clock: Callable[[], float]) -> None:
        """Hold values for `ttl`, measured by `clock`."""
        self._ttl = ttl.total_seconds()
        self._clock = clock
        self._entries: dict[K, _Cached[V]] = {}

    def get(self, key: K) -> V | None:
        """The cached value, or `None` if it is missing or stale."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del self._entries[key]
            return None
        return entry.value

    def put(self, key: K, value: V) -> V:
        """Cache `value` for the TTL, and hand it straight back."""
        self._entries[key] = _Cached(value=value, expires_at=self._clock() + self._ttl)
        return value


class PermissionResolver:
    """Answers the questions :mod:`timothy_api.policy` asks, against Discord."""

    def __init__(
        self,
        discord: DiscordPort,
        *,
        ttl: timedelta,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Resolve through `discord`, remembering each answer for `ttl`."""
        self._discord = discord
        self._administrator: TtlCache[tuple[int, int], bool] = TtlCache(ttl, clock)
        self._membership: TtlCache[tuple[int, frozenset[int]], bool] = TtlCache(ttl, clock)
        self._roles: TtlCache[tuple[int, int], frozenset[int]] = TtlCache(ttl, clock)

    async def is_administrator(self, *, guild_id: int, user_id: int) -> bool:
        """Whether the user holds `ADMINISTRATOR` in this guild.

        A guild Timothy is not in resolves to `False`: authority over a guild is only
        meaningful here if Timothy is there to act on it.
        """
        cached = self._administrator.get((guild_id, user_id))
        if cached is not None:
            return cached
        try:
            permissions = await self._discord.guild_permissions(
                guild_id=guild_id, user_id=user_id
            )
        except NotFoundError:
            return self._administrator.put((guild_id, user_id), value=False)
        return self._administrator.put((guild_id, user_id), value=permissions.administrator)

    async def holds_any_role(
        self, *, guild_id: int, role_ids: frozenset[int], user_id: int
    ) -> bool:
        """Whether the user holds one of these roles in this guild (ADR 0012).

        Cached against the roles the member *has*, not against the question asked, so
        that changing `POOL_MANAGER_ROLE_IDS` takes effect on the next request rather
        than at the end of a TTL that nobody can see. A non-member holds nothing, which
        is the same answer as a member with no roles and wants no distinguishing: neither
        may manage pools.

        An empty `role_ids` is refused by the caller before it gets here, so this never
        answers `True` for a member of the guild who holds nothing.
        """
        held = self._roles.get((guild_id, user_id))
        if held is None:
            held = self._roles.put(
                (guild_id, user_id), value=await self._role_ids(guild_id, user_id)
            )
        return bool(held & role_ids)

    async def _role_ids(self, guild_id: int, user_id: int) -> frozenset[int]:
        """Ask Discord for a member's roles. A guild Timothy is not in yields none."""
        try:
            member = await self._discord.fetch_member(guild_id=guild_id, user_id=user_id)
        except NotFoundError:
            return frozenset()
        return frozenset() if member is None else member.role_ids

    async def is_member_of_any(self, *, guild_ids: Iterable[int], user_id: int) -> bool:
        """Whether the user is in any of these guilds.

        Cached against the aggregate rather than per guild, because the expensive answer
        is the negative one: a member is found and the scan stops, while a non-member
        costs a call per guild. Caching the aggregate bounds that to once per TTL. The
        cost of that is a guild Timothy has only just joined, which can take up to the
        TTL to count.

        The key includes *which* guilds were asked about, as a set, and that is not
        incidental. Browser callers are scanned against the intersection of Timothy's
        guilds with their own (ADR 0010), so "no" from one caller is only "no" about the
        guilds that caller was scanned over — keying on the user alone would let a
        browser's narrow miss answer the bot's wide question. Ordering is not part of the
        key, so `X-Timothy-From-Guild` still shares one entry across every hint.
        """
        order = list(guild_ids)
        scanned = frozenset(order)
        cached = self._membership.get((user_id, scanned))
        if cached is not None:
            return cached
        for guild_id in order:
            try:
                member = await self._discord.fetch_member(guild_id=guild_id, user_id=user_id)
            except NotFoundError:
                continue  # Timothy is not in that guild after all.
            if member is not None:
                return self._membership.put((user_id, scanned), value=True)
        return self._membership.put((user_id, scanned), value=False)
