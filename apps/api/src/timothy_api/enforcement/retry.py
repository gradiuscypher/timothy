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
from typing import TYPE_CHECKING, Final

from timothy_core.ports.discord import DiscordUnavailableError, RateLimitedError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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
            await sleep(error.retry_after)
        except DiscordUnavailableError:
            if attempt == max_attempts:
                raise
            await sleep(delay)
            delay *= 2
    msg = "unreachable: the loop either returns or raises"  # pragma: no cover
    raise AssertionError(msg)  # pragma: no cover
