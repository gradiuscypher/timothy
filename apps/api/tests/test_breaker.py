"""The circuit breaker: the rail that catches a bad migration before it lands.

ADR 0007's trade, stated plainly: a legitimate bulk listing *will* trip this and need an
explicit resume. That is intended. The alternative is that the one action Timothy cannot
take back happens twenty thousand times because somebody imported the wrong file.

The limit is three here so the tests can be read. In production it is twenty-five.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from timothy_api.audit import AuditAction
from timothy_api.settings import Settings
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    CHANNEL,
    GUILD,
    GUILD_ADMIN,
    POOL_ADMIN,
    Enforcement,
    headers,
    outcomes_of,
)

LIMIT = 3
CROWD = [300_000_000_000_000_010 + n for n in range(LIMIT + 2)]


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"enforcement_burst_limit": LIMIT}


@pytest.fixture
def crowded(pool: TestClient, discord: FakeDiscord, enforcement: Enforcement) -> TestClient:
    """A pool listing more people than one guild may be banned for in a single run.

    The listings are added and drained *before* anyone subscribes, so the burst arrives
    as one fan-out rather than as one job per listing. That is the shape the breaker is
    looking for — see `test_the_threshold_is_per_run`.
    """
    for user_id in CROWD:
        discord.add_member(GUILD, user_id)
        response = pool.post(
            "/pools/spam/listings",
            json={"user_id": str(user_id), "reason": "bulk import"},
            headers=headers(POOL_ADMIN),
        )
        assert response.status_code == 201
    enforcement.drain()
    return pool


def subscribe(client: TestClient) -> None:
    client.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )


def test_the_threshold_is_per_run(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """Listing people one at a time does not trip it, and should not.

    The breaker is looking for a burst — a bad migration, an accidental bulk listing —
    which arrives as a single fan-out. A guild that legitimately takes more than the
    limit over the course of many separate listings is a guild doing ordinary work, and
    stopping it would be the false positive that gets the rail switched off.
    """
    subscribe(pool)
    for user_id in CROWD:
        discord.add_member(GUILD, user_id)
        pool.post(
            "/pools/spam/listings",
            json={"user_id": str(user_id), "reason": "one at a time"},
            headers=headers(POOL_ADMIN),
        )
        enforcement.drain()

    assert sum(discord.is_banned(GUILD, user_id) for user_id in CROWD) == len(CROWD)
    assert (
        pool.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN)).json()["enforcement_paused"]
        is False
    )


def test_a_fan_out_past_the_limit_stops_at_the_limit(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    subscribe(crowded)
    enforcement.drain()

    assert sum(discord.is_banned(GUILD, user_id) for user_id in CROWD) == LIMIT


def test_tripping_pauses_the_guild_until_a_human_resumes_it(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """ "Marks the guild degraded, then requires a manual resume" (ADR 0007)."""
    subscribe(crowded)
    enforcement.drain()

    guild = crowded.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN)).json()
    assert guild["enforcement_paused"] is True


def test_the_pause_stops_the_next_job_too(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """Not just the run that tripped it. Otherwise the next listing would carry on where
    the bad import left off."""
    subscribe(crowded)
    enforcement.drain()
    banned = sum(discord.is_banned(GUILD, user_id) for user_id in CROWD)

    enforcement.sweep()
    enforcement.drain()

    assert sum(discord.is_banned(GUILD, user_id) for user_id in CROWD) == banned


def test_resuming_carries_on_from_where_it_stopped(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The bans already issued stay — halting is what the rail does, undoing is what
    `revert` is for — and a fresh run gets a fresh allowance."""
    subscribe(crowded)
    enforcement.drain()

    crowded.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": False}, headers=headers(GUILD_ADMIN)
    )
    enforcement.drain()

    assert sum(discord.is_banned(GUILD, user_id) for user_id in CROWD) == len(CROWD)


def test_tripping_is_recorded_and_the_guild_is_told(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The guild's own moderators are the ones who have to decide whether the burst was
    legitimate, so they are told what stopped and what to do."""
    crowded.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )
    subscribe(crowded)
    enforcement.drain()

    entries = crowded.get("/audit-log", headers=headers(POOL_ADMIN)).json()
    tripped = next(
        entry
        for entry in entries
        if entry["action"] == AuditAction.ENFORCEMENT_BREAKER_TRIPPED.value
    )
    assert tripped["detail"]["burst_limit"] == LIMIT
    assert any("Enforcement paused here" in message.content for message in discord.messages)


def test_a_guild_with_no_channel_is_still_paused(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The notice is a courtesy; the pause is the point."""
    subscribe(crowded)
    enforcement.drain()

    assert (
        crowded.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN)).json()[
            "enforcement_paused"
        ]
        is True
    )


def test_the_bans_that_landed_are_still_attributed(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """So that `revert` can undo the bad import, which is the usual next step."""
    subscribe(crowded)
    enforcement.drain()

    assert len([row for row in outcomes_of(settings) if row["status"] == "banned"]) == LIMIT


# -- the same rail, for notifications ----------------------------------------


def subscribe_at_warn(client: TestClient) -> None:
    client.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "warn"},
        headers=headers(GUILD_ADMIN),
    )


def test_a_warn_fan_out_past_the_limit_stops_at_the_limit(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """A warn-level subscription turns the same bad listing into a burst of messages
    rather than a burst of bans, and a channel receiving three thousand of them is the
    same accident wearing a different hat.

    The migration data found this: one guild held three pools at `warn`, standing exposure
    2,935 notifications, and nothing capped it.
    """
    crowded.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )
    subscribe_at_warn(crowded)
    enforcement.drain()

    warnings = [message for message in discord.messages if "Heads up" in message.content]
    assert len(warnings) == LIMIT


def test_a_warn_fan_out_past_the_limit_pauses_the_guild(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    crowded.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )
    subscribe_at_warn(crowded)
    enforcement.drain()

    guild = crowded.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN)).json()
    assert guild["enforcement_paused"] is True


def test_bans_and_warnings_share_one_budget(
    crowded: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The limit is on what Timothy does to a guild in one run, not on either kind
    separately. A guild holding one pool at ban and another at warn gets one allowance
    between them, because the accident the rail catches does not care which it is.
    """
    crowded.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )
    subscribe_at_warn(crowded)
    enforcement.drain()

    warnings = sum("Heads up" in message.content for message in discord.messages)
    bans = sum(discord.is_banned(GUILD, user_id) for user_id in CROWD)

    assert warnings + bans == LIMIT
