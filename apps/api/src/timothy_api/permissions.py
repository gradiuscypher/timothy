"""Resolving a caller's authority against Discord, with a short-lived cache.

Every authorised action costs a permission lookup inside Discord's three-second
interaction deadline (ADR 0003), so the answers are cached for `PERMISSION_CACHE_TTL`.
The cache is per process and in memory: it is a rate-limit shield, not a store, and
losing it on restart costs one extra call.

Two questions get asked, and they need different calls. Whether someone is an
administrator is `guild_permissions`, which answers `none()` for a non-member and so
cannot tell "not here" from "here with nothing". Whether someone is a member at all is
therefore `fetch_member`.
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
        self._membership: TtlCache[int, bool] = TtlCache(ttl, clock)

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

    async def is_member_of_any(self, *, guild_ids: Iterable[int], user_id: int) -> bool:
        """Whether the user is in any guild Timothy is in.

        Cached against the user rather than the pair, because the expensive answer is
        the negative one: a member is found and the scan stops, while a non-member costs
        a call per guild. Caching the aggregate bounds that to once per TTL. The cost of
        that is a guild Timothy has only just joined, which can take up to the TTL to
        count. Phase 6 can shortcut this for browser callers, whose OAuth `guilds` scope
        already names the guilds they are in.
        """
        cached = self._membership.get(user_id)
        if cached is not None:
            return cached
        for guild_id in guild_ids:
            try:
                member = await self._discord.fetch_member(guild_id=guild_id, user_id=user_id)
            except NotFoundError:
                continue  # Timothy is not in that guild after all.
            if member is not None:
                return self._membership.put(user_id, value=True)
        return self._membership.put(user_id, value=False)
