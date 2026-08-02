"""Backing off around one Discord call.

The distinction this file exists to pin down: a rate limit or an outage is not a failure
of the *work* — the ban is still the right thing to do — so it is retried here, around
the call, rather than by failing the job. A refusal is a failure of the work, and is not
retried at all, because the next attempt will collect the same refusal.
"""

import pytest

from timothy_api.enforcement.retry import MAX_ATTEMPTS, with_backoff
from timothy_core.ports.discord import (
    DiscordUnavailableError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
)

WAITS: list[float] = []


async def record_wait(seconds: float) -> None:
    WAITS.append(seconds)


@pytest.fixture(autouse=True)
def _clear_waits() -> None:
    WAITS.clear()


@pytest.mark.anyio
async def test_a_call_that_works_is_made_once() -> None:
    calls = 0

    async def succeed() -> str:
        nonlocal calls
        calls += 1
        return "done"

    assert await with_backoff(succeed, sleep=record_wait) == "done"
    assert calls == 1
    assert WAITS == []


@pytest.mark.anyio
async def test_a_rate_limit_is_waited_out_for_as_long_as_discord_asked() -> None:
    """Discord says how long; believing it is the whole point of carrying `retry_after`."""
    attempts = 0

    async def rate_limited_once() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RateLimitedError(2.5)
        return "done"

    assert await with_backoff(rate_limited_once, sleep=record_wait) == "done"
    assert WAITS == [2.5]


@pytest.mark.anyio
async def test_an_outage_backs_off_geometrically() -> None:
    """An outage says nothing about how long, so this is a guess — and a guess that keeps
    doubling rather than hammering."""

    async def unavailable() -> str:
        raise DiscordUnavailableError

    with pytest.raises(DiscordUnavailableError):
        await with_backoff(unavailable, sleep=record_wait)

    assert WAITS == [2.0, 4.0, 8.0]


@pytest.mark.anyio
async def test_retrying_gives_up_and_re_raises() -> None:
    """So the caller can record a durable `failed` outcome rather than looping forever."""
    attempts = 0

    async def always_rate_limited() -> str:
        nonlocal attempts
        attempts += 1
        raise RateLimitedError(0.1)

    with pytest.raises(RateLimitedError):
        await with_backoff(always_rate_limited, sleep=record_wait)

    assert attempts == MAX_ATTEMPTS


@pytest.mark.anyio
@pytest.mark.parametrize("error", [ForbiddenError("no permission"), NotFoundError("gone")])
async def test_a_refusal_is_not_retried(error: Exception) -> None:
    """A guild that granted Timothy no ban permission will refuse the next attempt too.
    The durable answer is a `failed` outcome the sweep picks up once that changes."""
    attempts = 0

    async def refused() -> str:
        nonlocal attempts
        attempts += 1
        raise error

    with pytest.raises(type(error)):
        await with_backoff(refused, sleep=record_wait)

    assert attempts == 1
    assert WAITS == []
