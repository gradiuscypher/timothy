"""Dry run: record everything, issue nothing.

The mode phase 5 rehearses the whole cutover in, and the mode Timothy falls back to when
it cannot read the setting. The decision worth testing here is *where* it records:

CONTEXT.md says dry run "records every enforcement it would perform", and PLAN.md's phase
5 diffs those intentions against the old bot. But `enforcement_outcomes` is not a record
of intentions — it is the attribution that the revert path acts on. A `banned` row for a
ban that was never issued would have Timothy unban a user it never touched, the moment
dry run came off. So the intended action goes to the audit log, and the durable state
stays empty.
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
    LISTED_USER,
    POOL_ADMIN,
    Enforcement,
    headers,
    outcomes_of,
)


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"dry_run": True}


def dry_run_entries(client: TestClient) -> list[dict[str, Any]]:
    entries = client.get("/audit-log", headers=headers(POOL_ADMIN)).json()
    return [
        entry for entry in entries if entry["action"] == AuditAction.ENFORCEMENT_DRY_RUN.value
    ]


def listed_and_subscribed(client: TestClient, discord: FakeDiscord, level: str = "ban") -> None:
    client.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": level},
        headers=headers(GUILD_ADMIN),
    )
    discord.add_member(GUILD, LISTED_USER)
    client.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "raiding"},
        headers=headers(POOL_ADMIN),
    )


def test_nothing_reaches_discord(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    listed_and_subscribed(pool, discord)

    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
    assert discord.calls_of("ban") == []


def test_the_intended_ban_is_recorded_in_the_audit_log(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """What phase 5 diffs against the old bot's behaviour.

    Note that the same intention can be logged more than once. Dry run writes no
    outcomes, so there is no `banned` row to make the next pass skip this user — every
    sweep restates what it would do. For a diff that is correct; for a dedupe it would
    not be, which is one more reason these are audit lines and not outcomes.
    """
    listed_and_subscribed(pool, discord)

    enforcement.drain()

    entries = dry_run_entries(pool)
    assert entries
    assert {entry["detail"]["would"] for entry in entries} == {"ban"}
    assert {entry["target"] for entry in entries} == {f"guild:{GUILD}/user:{LISTED_USER}"}


def test_no_attribution_is_written(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """The one that matters. A `banned` row here would arm the revert path against bans
    that were never issued."""
    listed_and_subscribed(pool, discord)

    enforcement.drain()

    assert outcomes_of(settings) == []


def test_an_exception_is_still_recorded(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """The carve-out ADR 0009 makes explicit, pinned so nobody closes it as a bug.

    `skipped_exception` is not an attribution — it says the guild vouched for this user,
    which is true whether or not dry run is on, and which no revert can act on because
    reverting keys strictly on a `banned` outcome. It also stops the sweep asking Discord
    about that user every round, which at a couple of member lookups a second per guild is
    not a rounding error.

    Found by a rehearsal against production data, where three real vouches turned up in
    the outcomes table and looked, briefly, like dry run leaking.
    """
    listed_and_subscribed(pool, discord)
    pool.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}",
        json={"reason": "vouched for"},
        headers=headers(GUILD_ADMIN),
    )

    enforcement.drain()

    assert [row["status"] for row in outcomes_of(settings)] == ["skipped_exception"]
    assert not discord.calls_of("ban")


def test_a_warning_is_described_rather_than_posted(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    pool.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )
    listed_and_subscribed(pool, discord, level="warn")

    enforcement.drain()

    assert discord.messages == []
    assert dry_run_entries(pool)[0]["detail"]["would"] == "warn"


def test_a_revert_is_described_rather_than_issued(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """Dry run means Timothy issues nothing, in either direction.

    Getting here needs a ban that really was issued, so the setup runs with dry run off —
    which is the real situation this guards: an operator switching dry run *on* to freeze
    Timothy while they investigate must find the reverts frozen too, not quietly running.
    """
    live = Enforcement(
        settings.model_copy(update={"dry_run": False}), discord, enforcement.self_unbans
    )
    listed_and_subscribed(pool, discord)
    live.drain()
    assert discord.is_banned(GUILD, LISTED_USER)

    pool.delete("/pools/spam?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)
    assert discord.calls_of("unban") == []
    assert any(entry["detail"]["would"] == "revert" for entry in dry_run_entries(pool))


def test_the_attribution_survives_a_frozen_revert(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """Clearing it would leave the ban in place with nothing recording that Timothy made
    it, so turning dry run off again would find the revert no longer possible."""
    live = Enforcement(
        settings.model_copy(update={"dry_run": False}), discord, enforcement.self_unbans
    )
    listed_and_subscribed(pool, discord)
    live.drain()

    pool.delete("/pools/spam?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert [row["status"] for row in outcomes_of(settings)] == ["banned"]


def test_the_breaker_still_halts_but_does_not_pause_real_guilds(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """A rehearsal against production data must not leave real guilds paused when dry run
    comes off — but it must still show that the run *would* have been stopped."""
    for offset in range(enforcement.settings.enforcement_burst_limit + 2):
        user_id = 300_000_000_000_000_100 + offset
        discord.add_member(GUILD, user_id)
        pool.post(
            "/pools/spam/listings",
            json={"user_id": str(user_id), "reason": "bulk"},
            headers=headers(POOL_ADMIN),
        )
    enforcement.drain()

    # Subscribing last makes the burst one fan-out, which is the shape the breaker looks
    # for.
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    enforcement.drain()

    entries = pool.get("/audit-log", headers=headers(POOL_ADMIN)).json()
    tripped = next(
        entry
        for entry in entries
        if entry["action"] == AuditAction.ENFORCEMENT_BREAKER_TRIPPED.value
    )
    assert tripped["detail"]["dry_run"] is True
    assert (
        pool.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN)).json()["enforcement_paused"]
        is False
    )
