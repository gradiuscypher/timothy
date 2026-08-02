"""Enforcement, end to end: a moderator types something and somebody gets banned.

These drive the real API to change the world, then run the real worker over the real
queue against the in-memory Discord. Nothing is stubbed between the two — which is the
point of ADR 0007, and the reason a test can assert both that a ban was issued and that
`enforcement_outcomes` now says Timothy issued it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from timothy_api.audit import AuditAction
from timothy_api.settings import Settings
from timothy_core.enums import OutcomeStatus
from timothy_core.ports.discord import ForbiddenError, NotFoundError
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    CHANNEL,
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    OTHER_GUILD,
    POOL_ADMIN,
    Enforcement,
    headers,
    jobs_of,
    outcomes_of,
)

OTHER_USER = 300_000_000_000_000_002


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    """Pin the sweep interval rather than inheriting the production one.

    These tests advance a clock past a staggered job's `run_after`, so they depend on the
    stagger being small. The production default is a week (PLAN.md, "What a sweep costs"),
    which is a fact about the migrated deployment's size and not something a unit test
    should be reading.
    """
    return {"sweep_interval": timedelta(hours=1)}


def later() -> datetime:
    """A clock past the whole sweep interval.

    A sweep round dates its guilds forward across the interval so they do not all land at
    once, so the second guild's job is not due for half an hour. Draining "now" would
    quietly run nothing and every sweep assertion would pass for the wrong reason.
    """
    return datetime.now(UTC) + timedelta(hours=2)


def subscribe(
    client: TestClient, guild_id: int, level: str = "ban", pool: str = "spam"
) -> None:
    client.put(
        f"/guilds/{guild_id}/subscriptions/{pool}",
        json={"level": level},
        headers=headers(GUILD_ADMIN),
    )


def add_listing(client: TestClient, user_id: int, reason: str = "raiding") -> None:
    response = client.post(
        "/pools/spam/listings",
        json={"user_id": str(user_id), "reason": reason},
        headers=headers(POOL_ADMIN),
    )
    assert response.status_code == 201


def set_channel(client: TestClient, guild_id: int = GUILD) -> None:
    client.put(
        f"/guilds/{guild_id}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )


def audit_actions(client: TestClient) -> list[str]:
    entries = client.get("/audit-log", headers=headers(POOL_ADMIN)).json()
    return [entry["action"] for entry in entries]


# -- the ban path ------------------------------------------------------------


def test_listing_a_present_user_bans_them(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """ADR 0004: creating a listing no longer waits for a join or an hourly sweep."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)

    add_listing(pool, LISTED_USER)
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)


def test_the_ban_is_recorded_as_timothys(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """The row that makes the ban attributable, and so revertable (ADR 0005)."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER, reason="raiding")

    enforcement.drain()

    assert [
        (row["guild_id"], row["user_id"], row["status"], row["reason"])
        for row in outcomes_of(settings)
    ] == [(GUILD, LISTED_USER, OutcomeStatus.BANNED.value, "raiding")]


def test_the_discord_audit_reason_names_the_pool(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """A moderator reading their own guild's audit log should not have to ask Timothy
    which subscription caused this."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER, reason="raiding")

    enforcement.drain()

    reason = discord.ban_reason(GUILD, LISTED_USER)
    assert reason is not None
    assert "spam" in reason
    assert "raiding" in reason


def test_timothys_ban_is_in_the_audit_log_as_timothys(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)

    enforcement.drain()

    entries = pool.get("/audit-log", headers=headers(POOL_ADMIN)).json()
    ban = next(
        entry for entry in entries if entry["action"] == AuditAction.ENFORCEMENT_BAN.value
    )
    assert ban["actor"] == "system"
    assert ban["target"] == f"guild:{GUILD}/user:{LISTED_USER}"


def test_an_absent_user_is_not_banned_and_nothing_is_recorded(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """Enforcement is reactive (ADR 0004). Recording nothing is what leaves the door
    armed for the day they turn up."""
    subscribe(pool, GUILD)

    add_listing(pool, LISTED_USER)
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
    assert outcomes_of(settings) == []


def test_a_listing_reaches_every_subscribing_guild(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    pool.put(f"/guilds/{OTHER_GUILD}", headers=headers("system"))
    discord.add_member(OTHER_GUILD, GUILD_ADMIN, permissions=None)
    subscribe(pool, GUILD)
    # OTHER_GUILD's administrator is the same person, made one there for this.
    discord.guilds[OTHER_GUILD].permissions[GUILD_ADMIN] = discord.guilds[GUILD].permissions[
        GUILD_ADMIN
    ]
    subscribe(pool, OTHER_GUILD)
    discord.add_member(GUILD, LISTED_USER)
    discord.add_member(OTHER_GUILD, LISTED_USER)

    add_listing(pool, LISTED_USER)
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)
    assert discord.is_banned(OTHER_GUILD, LISTED_USER)


def test_a_guild_that_does_not_subscribe_is_untouched(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    pool.put(f"/guilds/{OTHER_GUILD}", headers=headers("system"))
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    discord.add_member(OTHER_GUILD, LISTED_USER)

    add_listing(pool, LISTED_USER)
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)
    assert not discord.is_banned(OTHER_GUILD, LISTED_USER)


def test_subscribing_enforces_the_whole_pool(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The fan-out in the other direction: the guild is new, the listings are not."""
    add_listing(pool, LISTED_USER)
    add_listing(pool, OTHER_USER)
    discord.add_member(GUILD, LISTED_USER)
    discord.add_member(GUILD, OTHER_USER)
    enforcement.drain()

    subscribe(pool, GUILD)
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)
    assert discord.is_banned(GUILD, OTHER_USER)


def test_raising_warn_to_ban_enforces_but_lowering_does_not(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """Asking to stop banning from now on is not asking to undo what was already done."""
    set_channel(pool)
    subscribe(pool, GUILD, level="warn")
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)
    enforcement.drain()
    assert not discord.is_banned(GUILD, LISTED_USER)

    subscribe(pool, GUILD, level="ban")
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)


# -- the warn path -----------------------------------------------------------


def test_a_warn_subscription_posts_and_never_bans(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    set_channel(pool)
    subscribe(pool, GUILD, level="warn")
    discord.add_member(GUILD, LISTED_USER)

    add_listing(pool, LISTED_USER, reason="raiding")
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
    assert len(discord.messages) == 1
    assert str(LISTED_USER) in discord.messages[0].content
    assert "raiding" in discord.messages[0].content


def test_a_user_is_warned_about_once_and_never_again(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The `warned` outcome is simultaneously the audit trail and the dedupe key."""
    set_channel(pool)
    subscribe(pool, GUILD, level="warn")
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)
    enforcement.drain()

    enforcement.sweep()
    enforcement.drain(now=later)

    assert len(discord.messages) == 1


def test_a_warn_with_nowhere_to_report_fails_rather_than_dedupes(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """A `warned` row here would silently consume the one warning this user ever gets."""
    subscribe(pool, GUILD, level="warn")
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)

    enforcement.drain()

    assert [row["status"] for row in outcomes_of(settings)] == [OutcomeStatus.FAILED.value]


def test_setting_a_channel_later_delivers_the_missed_warning(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    subscribe(pool, GUILD, level="warn")
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)
    enforcement.drain()
    assert discord.messages == []

    set_channel(pool)
    enforcement.sweep()
    enforcement.drain(now=later)

    assert len(discord.messages) == 1


def test_a_ban_level_pool_silences_a_warn_level_one(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The warn copy describes a counterfactual, and once the user is banned it is not
    counterfactual any more."""
    set_channel(pool)
    pool.post("/pools", json={"name": "raiders"}, headers=headers(POOL_ADMIN))
    subscribe(pool, GUILD, level="warn")
    subscribe(pool, GUILD, level="ban", pool="raiders")
    discord.add_member(GUILD, LISTED_USER)

    add_listing(pool, LISTED_USER)
    pool.post(
        "/pools/raiders/listings",
        json={"user_id": str(LISTED_USER), "reason": "raiding"},
        headers=headers(POOL_ADMIN),
    )
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)
    assert discord.messages == []


# -- the skips ---------------------------------------------------------------


def test_an_exception_stops_the_ban_and_is_recorded(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """ "We deliberately did nothing" is the one skip a moderator later asks about."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    pool.put(f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN))

    add_listing(pool, LISTED_USER)
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
    assert [row["status"] for row in outcomes_of(settings)] == [
        OutcomeStatus.SKIPPED_EXCEPTION.value
    ]


def test_withdrawing_an_exception_lets_enforcement_look_again(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The `skipped_exception` row would otherwise have the sweep skip this user forever."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    pool.put(f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN))
    add_listing(pool, LISTED_USER)
    enforcement.drain()

    pool.delete(f"/guilds/{GUILD}/exceptions/{LISTED_USER}", headers=headers(GUILD_ADMIN))
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)


def test_an_exception_suppresses_warnings_too(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The copy says a ban would have happened, which is what an exception rules out."""
    set_channel(pool)
    subscribe(pool, GUILD, level="warn")
    discord.add_member(GUILD, LISTED_USER)
    pool.put(f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN))

    add_listing(pool, LISTED_USER)
    enforcement.drain()

    assert discord.messages == []


def test_a_paused_guild_records_nothing_and_resuming_catches_up(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """Recording `enforcement_paused` would survive the resume, which is exactly wrong."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    pool.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": True}, headers=headers(GUILD_ADMIN)
    )

    add_listing(pool, LISTED_USER)
    enforcement.drain()
    assert not discord.is_banned(GUILD, LISTED_USER)
    assert outcomes_of(settings) == []

    pool.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": False}, headers=headers(GUILD_ADMIN)
    )
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)


def test_a_guild_timothy_has_left_is_not_an_error(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """Jobs outlive the thing that queued them."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)
    pool.delete(f"/guilds/{GUILD}", headers=headers("system"))

    enforcement.drain()
    assert not discord.is_banned(GUILD, LISTED_USER)


# -- when Discord says no ----------------------------------------------------


def test_a_refused_ban_is_recorded_and_the_rest_of_the_fan_out_lands(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """The everyday case: a guild that granted Timothy no ban permission."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    discord.add_member(GUILD, OTHER_USER)
    discord.fail(
        "ban", guild_id=GUILD, user_id=LISTED_USER, error=ForbiddenError("missing permission")
    )

    add_listing(pool, LISTED_USER)
    add_listing(pool, OTHER_USER)
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
    assert discord.is_banned(GUILD, OTHER_USER)
    statuses = {row["user_id"]: row["status"] for row in outcomes_of(settings)}
    assert statuses == {
        LISTED_USER: OutcomeStatus.FAILED.value,
        OTHER_USER: OutcomeStatus.BANNED.value,
    }


def test_a_refusal_does_not_fail_the_job(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """Running the same job again in eight seconds would collect the same refusal. The
    `failed` outcome is what the sweep retries, once the world may have changed."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    discord.fail("ban", guild_id=GUILD, user_id=LISTED_USER, error=ForbiddenError("nope"))
    add_listing(pool, LISTED_USER)

    enforcement.drain()

    assert {job["status"] for job in jobs_of(settings)} == {"done"}


def test_a_failed_outcome_is_retried_by_the_sweep(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """ADR 0004's "retroactive ban failure correction", which is why `failed` is not a
    settled status."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    discord.fail("ban", guild_id=GUILD, user_id=LISTED_USER, error=ForbiddenError("nope"))
    add_listing(pool, LISTED_USER)
    enforcement.drain()

    discord.clear_failures()
    enforcement.sweep()
    enforcement.drain(now=later)

    assert discord.is_banned(GUILD, LISTED_USER)


def test_a_settled_user_costs_the_sweep_nothing(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The reason the hourly safety net does not cost a `fetch_member` per listing."""
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)
    enforcement.drain()

    before = len(discord.calls)
    enforcement.sweep()
    enforcement.drain(now=later)

    assert len(discord.calls) == before


def test_a_rate_limit_that_never_lets_up_becomes_a_failed_outcome(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """Retrying is bounded. Once it runs out the answer is durable and the sweep owns it.

    That a rate limit is retried at all is :mod:`tests.test_retry`'s business; this is
    what happens when the retries do not help.
    """
    subscribe(pool, GUILD)
    discord.add_member(GUILD, LISTED_USER)
    add_listing(pool, LISTED_USER)
    discord.rate_limit_after(1, retry_after=0.01)

    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
    assert [row["status"] for row in outcomes_of(settings)] == [OutcomeStatus.FAILED.value]


def test_a_missing_notification_channel_is_recorded_not_raised(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """Timothy does not check it can post when the channel is set, because it cannot know
    whether it still can tomorrow."""
    set_channel(pool)
    subscribe(pool, GUILD, level="warn")
    discord.add_member(GUILD, LISTED_USER)
    discord.fail_message(CHANNEL, NotFoundError("channel deleted"))

    add_listing(pool, LISTED_USER)
    enforcement.drain()

    assert [row["status"] for row in outcomes_of(settings)] == [OutcomeStatus.FAILED.value]
