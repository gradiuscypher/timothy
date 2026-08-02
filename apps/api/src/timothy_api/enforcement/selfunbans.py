"""Remembering which unbans were Timothy's own.

ADR 0005's second consequence. Timothy's reverts raise `GUILD_BAN_REMOVE` on the gateway
exactly as a moderator's unban does, and ADR 0006's hook turns an unban into a permanent
exception. Left alone, a revert would exempt the very users it just readmitted and every
later enforcement of that listing would be a no-op.

In-process, with a TTL, and consumed on the first read. That is enough because the
backend is the only thing that unbans (ADR 0003), so it is also the only thing that can
know; and because the window between issuing an unban and the bot relaying the event
back is seconds. Losing the marker across a restart costs at most one spurious
exception, which a moderator can delete — the alternative, a durable table, would have
to be swept and would outlive its own usefulness.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_TTL: Final = 300.0
"""Five minutes. Long enough for a gateway relay that has had to reconnect, short enough
that a marker never survives into an unrelated manual unban of the same user."""


class SelfUnbans:
    """The unbans Timothy has issued and not yet seen come back to it."""

    def __init__(
        self, *, ttl: float = DEFAULT_TTL, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Remember each marker for `ttl` seconds, measured by `clock`."""
        self._ttl = ttl
        self._clock = clock
        self._marks: dict[tuple[int, int], float] = {}

    def mark(self, *, guild_id: int, user_id: int) -> None:
        """Record that Timothy is about to unban this user here.

        Marked *before* the call rather than after, so an unban that succeeds and then
        loses its acknowledgement is still recognised when the event arrives.
        """
        self._expire()
        self._marks[guild_id, user_id] = self._clock() + self._ttl

    def claim(self, *, guild_id: int, user_id: int) -> bool:
        """Whether this unban was Timothy's, consuming the marker if it was.

        Consumed because a marker answers for exactly one event. A moderator who unbans
        the same user again a minute later has done something Timothy did not do, and
        ADR 0006's hook should fire for it.
        """
        self._expire()
        return self._marks.pop((guild_id, user_id), None) is not None

    def _expire(self) -> None:
        now = self._clock()
        stale = [key for key, expires_at in self._marks.items() if expires_at <= now]
        for key in stale:
            del self._marks[key]
