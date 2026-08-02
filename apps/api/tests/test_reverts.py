"""Undoing bans Timothy issued — and refusing to touch the ones it did not.

ADR 0005. Every one of these paths is opt-in: the `revert` flag defaults off everywhere,
because the alternative is a moderator deleting a listing and silently readmitting people
to servers that never asked for them back.

The rule the whole file is really about is the negative one. A guild's own bans are never
lifted, no matter what the listings say, and the only evidence Timothy has that a ban is
its own is a recorded `banned` outcome.
"""

from fastapi.testclient import TestClient

from timothy_api.audit import AuditAction
from timothy_api.settings import Settings
from timothy_core.enums import OutcomeStatus
from timothy_core.ports.discord import ForbiddenError
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    POOL_ADMIN,
    Enforcement,
    headers,
    outcomes_of,
)


def banned_by_timothy(
    client: TestClient, discord: FakeDiscord, enforcement: Enforcement, *, pool: str = "spam"
) -> None:
    """Get to the state every test here starts from: Timothy has banned LISTED_USER."""
    client.put(
        f"/guilds/{GUILD}/subscriptions/{pool}",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    discord.add_member(GUILD, LISTED_USER)
    client.post(
        f"/pools/{pool}/listings",
        json={"user_id": str(LISTED_USER), "reason": "raiding"},
        headers=headers(POOL_ADMIN),
    )
    enforcement.drain()
    assert discord.is_banned(GUILD, LISTED_USER)


# -- the flag is opt-in ------------------------------------------------------


def test_removing_a_listing_leaves_the_ban_by_default(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    banned_by_timothy(pool, discord, enforcement)

    pool.delete(f"/pools/spam/listings/{LISTED_USER}", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)


def test_removing_a_listing_with_revert_lifts_the_ban(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    banned_by_timothy(pool, discord, enforcement)

    pool.delete(f"/pools/spam/listings/{LISTED_USER}?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
    assert outcomes_of(settings) == []


def test_unsubscribing_with_revert_lifts_the_bans_that_pool_caused(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    banned_by_timothy(pool, discord, enforcement)

    pool.delete(f"/guilds/{GUILD}/subscriptions/spam?revert=true", headers=headers(GUILD_ADMIN))
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)


def test_deleting_a_pool_with_revert_lifts_its_bans_everywhere(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The outcomes outlive the pool precisely so this can find them (ADR 0005)."""
    banned_by_timothy(pool, discord, enforcement)

    pool.delete("/pools/spam?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)


# -- attribution -------------------------------------------------------------


def test_a_guilds_own_ban_is_never_lifted(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """No recorded outcome means no evidence the ban is Timothy's, whatever the listings
    say. This is the rule the whole feature waited on."""
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "raiding"},
        headers=headers(POOL_ADMIN),
    )
    enforcement.drain()
    # The guild banned them itself, before Timothy ever saw them.
    discord.guilds[GUILD].bans[LISTED_USER] = "we banned this person ourselves"

    pool.delete(f"/pools/spam/listings/{LISTED_USER}?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)
    assert discord.ban_reason(GUILD, LISTED_USER) == "we banned this person ourselves"


def test_a_ban_another_live_listing_still_justifies_stays(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """One listing going away does not mean the ban has stopped being right.

    Note what holds the ban up: a *live listing* in a pool the guild still subscribes to
    at ban level, not a second recorded outcome. There is no second outcome to find — the
    user was already banned and therefore no longer in the guild when `raiders` listed
    them, so enforcement there correctly decided nothing. Asking the listings rather than
    the outcomes is what makes this case come out right.
    """
    pool.post("/pools", json={"name": "raiders"}, headers=headers(POOL_ADMIN))
    banned_by_timothy(pool, discord, enforcement)
    pool.put(
        f"/guilds/{GUILD}/subscriptions/raiders",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    pool.post(
        "/pools/raiders/listings",
        json={"user_id": str(LISTED_USER), "reason": "also raiding"},
        headers=headers(POOL_ADMIN),
    )
    enforcement.drain()

    pool.delete(f"/pools/spam/listings/{LISTED_USER}?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)
    # The pool that went away no longer claims to be part of why.
    assert outcomes_of(settings) == []


def test_a_lifted_ban_leaves_no_attribution_behind(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """A surviving `banned` row would have a later revert unban somebody already back."""
    banned_by_timothy(pool, discord, enforcement)

    pool.delete("/pools/spam?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert [row for row in outcomes_of(settings) if row["status"] == "banned"] == []


def test_reverting_is_recorded_in_the_audit_log(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    banned_by_timothy(pool, discord, enforcement)

    pool.delete("/pools/spam?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    entries = pool.get("/audit-log", headers=headers(POOL_ADMIN)).json()
    revert = next(
        entry for entry in entries if entry["action"] == AuditAction.ENFORCEMENT_REVERT.value
    )
    assert revert["actor"] == "system"


# -- reverting because of an exception ---------------------------------------


def test_an_exception_alone_does_not_lift_an_existing_ban(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The question phase 2 left open, answered the way every other revert is: opt-in."""
    banned_by_timothy(pool, discord, enforcement)

    pool.put(f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN))
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)


def test_an_exception_with_revert_lifts_the_ban_and_records_the_skip(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """The listing still justifies the ban — an exception is exactly the guild deciding
    that its subscriptions do not reach this person, so `still_justified` does not apply.
    """
    banned_by_timothy(pool, discord, enforcement)

    pool.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}?revert=true",
        json={},
        headers=headers(GUILD_ADMIN),
    )
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
    assert [row["status"] for row in outcomes_of(settings)] == [
        OutcomeStatus.SKIPPED_EXCEPTION.value
    ]


def test_an_exception_with_revert_still_will_not_touch_a_guilds_own_ban(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """Attribution is the one rule the exception flag does not get to bend."""
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    discord.guilds[GUILD].bans[LISTED_USER] = "our own ban"

    pool.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}?revert=true",
        json={},
        headers=headers(GUILD_ADMIN),
    )
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)


# -- when the unban itself fails ---------------------------------------------


def test_an_unban_discord_refuses_keeps_the_attribution(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """Clearing the row would tell a later revert the ban was never Timothy's, and the
    ban is still there. Keeping it means the next sweep or revert can try again."""
    banned_by_timothy(pool, discord, enforcement)
    discord.fail(
        "unban", guild_id=GUILD, user_id=LISTED_USER, error=ForbiddenError("no rights")
    )

    pool.delete("/pools/spam?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)
    assert [row["status"] for row in outcomes_of(settings)] == [OutcomeStatus.BANNED.value]
    entries = pool.get("/audit-log", headers=headers(POOL_ADMIN)).json()
    failure = next(
        entry for entry in entries if entry["action"] == AuditAction.ENFORCEMENT_FAILED.value
    )
    assert failure["detail"]["action"] == "revert"


def test_a_ban_somebody_else_already_lifted_still_clears_the_attribution(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """The row has stopped being true either way, and leaving it would have the next
    revert try to unban a user who is already back."""
    banned_by_timothy(pool, discord, enforcement)
    del discord.guilds[GUILD].bans[LISTED_USER]  # a moderator got there first

    pool.delete("/pools/spam?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert outcomes_of(settings) == []


def test_reverting_for_an_exception_when_there_is_nothing_to_revert(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The ordinary case for the flag: nobody was banned, so it is simply a no-op."""
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )

    response = pool.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}?revert=true",
        json={},
        headers=headers(GUILD_ADMIN),
    )
    enforcement.drain()

    assert response.status_code == 201
    assert discord.calls_of("unban") == []


# -- the pause rail does not block the remedy --------------------------------


def test_a_revert_works_even_while_the_guild_is_paused(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The usual reason a guild is paused is that the breaker just tripped on a bad bulk
    listing, and deleting that listing with `revert` is the fix."""
    banned_by_timothy(pool, discord, enforcement)
    pool.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": True}, headers=headers(GUILD_ADMIN)
    )

    pool.delete("/pools/spam?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
