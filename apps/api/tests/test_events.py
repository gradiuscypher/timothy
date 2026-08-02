"""Gateway events: the bot relays, the backend decides.

The delicate one is `ban-remove`. ADR 0006's hook turns a moderator's unban into a
permanent exception so the next sweep does not undo it — and ADR 0005 warns that
Timothy's *own* unbans raise the same event, which left alone would exempt exactly the
users a revert just readmitted.
"""

from typing import Any

from fastapi.testclient import TestClient

from timothy_api.settings import Settings
from timothy_core.enums import OutcomeStatus
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    MEMBER,
    POOL_ADMIN,
    Enforcement,
    headers,
    outcomes_of,
)


def event(client: TestClient, path: str, *, actor: int | str = "system") -> dict[str, str]:
    response = client.post(
        f"/events/{path}",
        json={"guild_id": str(GUILD), "user_id": str(LISTED_USER)},
        headers=headers(actor),
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


def subscribe_and_list(client: TestClient, discord: FakeDiscord) -> None:
    client.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    client.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "raiding"},
        headers=headers(POOL_ADMIN),
    )


def exceptions(client: TestClient) -> list[dict[str, Any]]:
    return list(client.get(f"/guilds/{GUILD}/exceptions", headers=headers(GUILD_ADMIN)).json())


# -- authorization -----------------------------------------------------------


def test_only_the_system_actor_may_relay_events(registered: TestClient) -> None:
    """A gateway event is something that happened, not something anyone asked for. A
    human asserting one would be asserting something untrue — and the exception this can
    create is Timothy's own, which no human route may produce."""
    for actor in (POOL_ADMIN, GUILD_ADMIN, MEMBER):
        response = registered.post(
            "/events/member-join",
            json={"guild_id": str(GUILD), "user_id": str(LISTED_USER)},
            headers=headers(actor),
        )
        assert response.status_code == 403


def test_relaying_still_needs_the_internal_token(registered: TestClient) -> None:
    response = registered.post(
        "/events/member-join",
        json={"guild_id": str(GUILD), "user_id": str(LISTED_USER)},
        headers=headers("system", token=None),
    )

    assert response.status_code == 401


# -- joining -----------------------------------------------------------------


def test_a_listed_user_is_banned_at_the_door(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """ADR 0004: a user listed while absent is banned when they turn up, not before."""
    subscribe_and_list(pool, discord)
    enforcement.drain()
    assert not discord.is_banned(GUILD, LISTED_USER)

    discord.add_member(GUILD, LISTED_USER)
    event(pool, "member-join")
    enforcement.drain()

    assert discord.is_banned(GUILD, LISTED_USER)


def test_an_unlisted_user_joining_costs_nothing(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """The overwhelmingly common event, and the one that must stay cheap."""
    discord.add_member(GUILD, LISTED_USER)

    event(pool, "member-join")
    enforcement.drain()

    assert not discord.is_banned(GUILD, LISTED_USER)
    assert outcomes_of(settings) == []


def test_an_event_for_a_guild_timothy_is_not_in_is_ignored(client: TestClient) -> None:
    assert "ignored" in event(client, "member-join")["action"]


# -- unbanning ---------------------------------------------------------------


def test_a_manual_unban_of_a_listed_user_creates_an_exception(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The hook exists so the next sweep does not simply undo the moderator."""
    subscribe_and_list(pool, discord)
    discord.add_member(GUILD, LISTED_USER)
    enforcement.drain()

    assert event(pool, "ban-remove")["action"] == "exception created"

    assert [row["user_id"] for row in exceptions(pool)] == [str(LISTED_USER)]


def test_the_auto_exception_is_attributed_to_timothy(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """Not to the magic user ID `"0"` the old bot used (ADR 0006)."""
    subscribe_and_list(pool, discord)
    discord.add_member(GUILD, LISTED_USER)
    enforcement.drain()

    event(pool, "ban-remove")

    assert exceptions(pool)[0]["created_by"] == "system"


def test_an_unban_of_an_unlisted_user_creates_nothing(pool: TestClient) -> None:
    """The old bot fired on every unban and filled the exception list with users who were
    never in a pool."""
    assert "no exception" in event(pool, "ban-remove")["action"]
    assert exceptions(pool) == []


def test_an_unban_of_a_user_listed_in_an_unsubscribed_pool_creates_nothing(
    pool: TestClient,
) -> None:
    """Nothing would undo this unban, so nothing needs to make it stick."""
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "raiding"},
        headers=headers(POOL_ADMIN),
    )

    assert "no exception" in event(pool, "ban-remove")["action"]
    assert exceptions(pool) == []


def test_an_unban_clears_the_stale_attribution(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement, settings: Settings
) -> None:
    """The `banned` row has stopped being true, whoever lifted the ban. Leaving it would
    have a later revert try to unban somebody who is already back."""
    subscribe_and_list(pool, discord)
    discord.add_member(GUILD, LISTED_USER)
    enforcement.drain()
    assert [row["status"] for row in outcomes_of(settings)] == [OutcomeStatus.BANNED.value]

    event(pool, "ban-remove")

    assert [row for row in outcomes_of(settings) if row["status"] == "banned"] == []


def test_timothys_own_unban_never_creates_an_exception(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """ADR 0005's second consequence. An exception here would exempt the very user the
    revert just readmitted, and every later enforcement of that listing would be a no-op
    forever."""
    subscribe_and_list(pool, discord)
    discord.add_member(GUILD, LISTED_USER)
    enforcement.drain()

    pool.delete(f"/guilds/{GUILD}/subscriptions/spam?revert=true", headers=headers(GUILD_ADMIN))
    enforcement.drain()
    assert not discord.is_banned(GUILD, LISTED_USER)

    assert event(pool, "ban-remove")["action"] == "ignored: Timothy's own revert"
    assert exceptions(pool) == []


def test_the_marker_answers_for_one_event_only(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """A moderator unbanning the same user again later has done something Timothy did
    not, and the hook should fire for it."""
    subscribe_and_list(pool, discord)
    discord.add_member(GUILD, LISTED_USER)
    enforcement.drain()
    pool.delete(f"/pools/spam/listings/{LISTED_USER}?revert=true", headers=headers(POOL_ADMIN))
    enforcement.drain()
    event(pool, "ban-remove")

    # Listed again, banned again by hand, and unbanned by hand.
    subscribe_and_list(pool, discord)
    assert event(pool, "ban-remove")["action"] == "exception created"


def test_an_existing_exception_is_not_duplicated(
    pool: TestClient, discord: FakeDiscord
) -> None:
    subscribe_and_list(pool, discord)
    pool.put(f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN))

    assert event(pool, "ban-remove")["action"] == "no exception: one already exists"
    assert len(exceptions(pool)) == 1
