"""Backing off, in the two places Discord asks us to.

A rate limit and an outage are not failures of the work — the ban is still the right
thing to do, Discord just cannot take it this second. They are handled here, around the
individual call, so that a fan-out of a hundred guilds does not burn a job's attempts on
pacing.

`ForbiddenError` and `NotFoundError` are not retried. A guild that granted Timothy no
ban permission will refuse the next attempt too, and the durable answer to that is a
`failed` enforcement outcome the sweep can pick up once the permission changes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from timothy_core.ports.discord import DiscordUnavailableError, RateLimitedError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

MAX_ATTEMPTS: Final = 4
UNAVAILABLE_BACKOFF: Final = 2.0
"""Seconds before the first retry of an unreachable Discord, doubling after that.

A rate limit says how long to wait and is believed; an outage says nothing, so this is a
guess, kept small because the job's own retry is the real backstop behind it.
"""


async def with_backoff[T](
    call: Callable[[], Awaitable[T]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_attempts: int = MAX_ATTEMPTS,
) -> T:
    """Run `call`, waiting out rate limits and brief outages.

    Raises:
        DiscordError: whatever the last attempt raised, once the attempts run out or the
            error is one that retrying cannot help.
    """
    delay = UNAVAILABLE_BACKOFF
    for attempt in range(1, max_attempts + 1):
        try:
            return await call()
        except RateLimitedError as error:
            if attempt == max_attempts:
                raise
            # WARNING, not DEBUG: a stall here is time the whole worker spends asleep,
            # because every Discord call the backend makes goes through it in turn. It is
            # the difference between a slow sweep and a wedged one, and it left no trace.
            log.warning(
                "rate limited, waiting %.1fs (attempt %d of %d)",
                error.retry_after,
                attempt,
                max_attempts,
                extra={"retry_after": error.retry_after, "attempt": attempt},
            )
            await sleep(error.retry_after)
        except DiscordUnavailableError as error:
            if attempt == max_attempts:
                raise
            log.warning(
                "discord unreachable, waiting %.1fs (attempt %d of %d): %s",
                delay,
                attempt,
                max_attempts,
                error,
                extra={"retry_after": delay, "attempt": attempt},
            )
            await sleep(delay)
            delay *= 2
    msg = "unreachable: the loop either returns or raises"  # pragma: no cover
    raise AssertionError(msg)  # pragma: no cover
