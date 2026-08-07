"""What the worker says while it is working.

These exist because a fan-out used to be opaque between `job N started` and `job N
finished`: a sweep of a large guild and a wedged one produced byte-identical logs. Every
assertion here is about a line an operator reads at 3am, so they check the `extra` fields
rather than the sentence — the message wording is free to change, the field names are the
interface the log store queries (ADR 0015).
"""

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from timothy_api.enforcement.retry import with_backoff
from timothy_api.settings import Settings
from timothy_core.ports.discord import RateLimitedError
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    POOL_MANAGER,
    Enforcement,
    headers,
)

ENGINE = "timothy_api.enforcement.engine"
HANDLERS = "timothy_api.enforcement.handlers"
RETRY = "timothy_api.enforcement.retry"

LIMIT = 3
CROWD = [300_000_000_000_000_010 + n for n in range(LIMIT + 2)]


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"enforcement_burst_limit": LIMIT}


def subscribe(client: TestClient, guild_id: int = GUILD, level: str = "ban") -> None:
    client.put(
        f"/guilds/{guild_id}/subscriptions/spam",
        json={"level": level},
        headers=headers(GUILD_ADMIN),
    )


def add_listing(client: TestClient, user_id: int, reason: str = "raiding") -> None:
    response = client.post(
        "/pools/spam/listings",
        json={"user_id": str(user_id), "reason": reason},
        headers=headers(POOL_MANAGER),
    )
    assert response.status_code == 201


def rows(caplog: pytest.LogCaptureFixture, logger: str, *keys: str) -> list[tuple[Any, ...]]:
    """The values of `keys` from every record of `logger` that carries all of them.

    Read with `getattr` rather than attribute access because `extra` fields are stapled
    onto `LogRecord` at runtime and the type checker cannot see them.
    """
    return [
        tuple(getattr(record, key) for key in keys)
        for record in caplog.records
        if record.name == logger and all(hasattr(record, key) for key in keys)
    ]


def fields(caplog: pytest.LogCaptureFixture, logger: str, key: str) -> list[Any]:
    """The value of one `extra` field, across every record from `logger` carrying it."""
    return [value for (value,) in rows(caplog, logger, key)]


# -- the per-pair line -------------------------------------------------------


def test_a_ban_logs_the_pair_and_the_decision(
    pool: TestClient,
    discord: FakeDiscord,
    enforcement: Enforcement,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The line that was missing: what the worker decided about one user in one guild."""
    subscribe(pool)
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)

    with caplog.at_level(logging.DEBUG, logger=ENGINE):
        enforcement.drain()

    assert (GUILD, LISTED_USER, "ban") in rows(
        caplog, ENGINE, "guild_id", "user_id", "decision"
    )


def test_a_skip_says_which_skip_it_was(
    pool: TestClient,
    discord: FakeDiscord,
    enforcement: Enforcement,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`not_listed` and `user_absent` are the two answers an operator actually asks for,
    and a bare "skip" would make them indistinguishable."""
    subscribe(pool)
    add_listing(pool, LISTED_USER)  # Listed, but never joined the guild.

    with caplog.at_level(logging.DEBUG, logger=ENGINE):
        enforcement.drain()

    assert "skip_user_absent" in fields(caplog, ENGINE, "decision")


def test_the_pair_line_is_debug_so_a_big_sweep_does_not_flood_info(
    pool: TestClient,
    discord: FakeDiscord,
    enforcement: Enforcement,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fan-out is thousands of pairs. At INFO this would be the whole log."""
    subscribe(pool)
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)

    with caplog.at_level(logging.INFO, logger=ENGINE):
        enforcement.drain()

    assert fields(caplog, ENGINE, "decision") == []


# -- the fan-out's own progress ----------------------------------------------


def test_a_fan_out_says_how_much_there_is_to_do(
    pool: TestClient,
    discord: FakeDiscord,
    enforcement: Enforcement,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The number that separates "large" from "stuck", which the start/finish pair on its
    own cannot answer."""
    subscribe(pool)
    for user_id in CROWD:
        discord.add_member(GUILD, user_id)
        add_listing(pool, user_id)

    with caplog.at_level(logging.INFO, logger=HANDLERS):
        enforcement.drain()

    assert fields(caplog, HANDLERS, "pair_total") != []


# -- the rails ---------------------------------------------------------------


def test_tripping_the_breaker_logs_a_warning(
    pool: TestClient,
    discord: FakeDiscord,
    enforcement: Enforcement,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """It wrote an audit row and nothing else. Nobody watches that table live."""
    for user_id in CROWD:
        discord.add_member(GUILD, user_id)
        add_listing(pool, user_id)
    enforcement.drain()

    with caplog.at_level(logging.WARNING, logger=ENGINE):
        subscribe(pool)  # One fan-out, larger than the burst limit.
        enforcement.drain()

    assert rows(caplog, ENGINE, "guild_id", "burst_limit", "levelname") == [
        (GUILD, LIMIT, "WARNING")
    ]


@pytest.mark.anyio
async def test_a_rate_limit_says_how_long_it_stalled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Time the worker spends asleep here is time every other job waits too, because they
    all go through the one worker. It used to leave no trace at all."""
    attempts = 0

    async def rate_limited_once() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RateLimitedError(2.5)
        return "done"

    async def instant(_seconds: float) -> None:
        return None

    with caplog.at_level(logging.WARNING, logger=RETRY):
        assert await with_backoff(rate_limited_once, sleep=instant) == "done"

    assert fields(caplog, RETRY, "retry_after") == [2.5]
