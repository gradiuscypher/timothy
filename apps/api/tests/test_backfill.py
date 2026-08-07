"""Asking Discord for the names traffic will never bring (ADR 0017).

The free sources only name people who turn up, and a user on a pool was listed precisely
so they would not. This is the round that goes and asks, and the properties that keep it
cheap: it looks an ID up once ever, it records a deleted account as looked-at so it never
asks twice, and it queues nothing on a day with nothing to do.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from timothy_api.settings import Settings
from timothy_core.migrations import sync_url
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    MEMBER,
    POOL_MANAGER,
    Enforcement,
    headers,
    insert_job,
    jobs_of,
)

OTHER_USER = 300_000_000_000_000_002
THIRD_USER = 300_000_000_000_000_003

BACKFILL = "backfill_user_names"


def names_of(settings: Settings) -> dict[int, str | None]:
    """Every row of the cache, names and misses alike — what `resolve` hides."""
    engine = create_engine(sync_url(settings.database_url))
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT user_id, name FROM user_names"))
            return {int(row[0]): row[1] for row in rows}
    finally:
        engine.dispose()


def resolved(client: TestClient, *user_ids: int) -> dict[str, str]:
    """What the UI would draw for these IDs."""
    response = client.get(
        "/users/names",
        params=[("id", str(user_id)) for user_id in user_ids],
        headers=headers(MEMBER),
    )
    assert response.status_code == 200, response.text
    return {row["user_id"]: row["name"] for row in response.json()}


def list_user(client: TestClient, user_id: int) -> None:
    response = client.post(
        "/pools/spam/listings",
        json={"user_id": str(user_id), "reason": "raiding"},
        headers=headers(POOL_MANAGER),
    )
    assert response.status_code == 201, response.text


@pytest.fixture
def listed(pool: TestClient) -> TestClient:
    """A pool with one listed user, whom nobody has ever seen a name for."""
    list_user(pool, LISTED_USER)
    return pool


# -- what a round looks up ---------------------------------------------------


def test_a_listed_user_gets_a_name_from_discord(
    listed: TestClient, enforcement: Enforcement, discord: FakeDiscord
) -> None:
    """The whole point: somebody listed years ago, who will never join anything."""
    discord.add_user(LISTED_USER, "Nuisance")

    assert enforcement.backfill() is True
    enforcement.drain()

    assert resolved(listed, LISTED_USER) == {str(LISTED_USER): "Nuisance"}


def test_an_excepted_user_gets_one_too(
    registered: TestClient, enforcement: Enforcement, discord: FakeDiscord
) -> None:
    """Exceptions are drawn on the same pages, so they are worth the same lookup."""
    discord.add_user(OTHER_USER, "Vouched")
    response = registered.put(
        f"/guilds/{GUILD}/exceptions/{OTHER_USER}",
        json={"reason": "our moderator"},
        headers=headers(GUILD_ADMIN),
    )
    assert response.status_code in {200, 201}, response.text

    enforcement.backfill()
    enforcement.drain()

    assert resolved(registered, OTHER_USER) == {str(OTHER_USER): "Vouched"}


def test_nobody_is_looked_up_twice(
    listed: TestClient, enforcement: Enforcement, discord: FakeDiscord
) -> None:
    """A name already known is not a reason to spend a Discord call."""
    discord.add_user(LISTED_USER, "Nuisance")
    enforcement.backfill()
    enforcement.drain()

    assert enforcement.backfill() is False
    enforcement.drain()

    assert len(discord.calls_of("fetch_user")) == 1


def test_a_deleted_account_is_recorded_as_looked_at(
    listed: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    """Without the row, every round for the rest of the deployment's life would ask
    again about the same accounts that no longer exist."""
    enforcement.backfill()
    enforcement.drain()

    assert names_of(settings) == {LISTED_USER: None}
    assert enforcement.backfill() is False


def test_an_id_with_no_name_still_draws_as_the_id(
    listed: TestClient, enforcement: Enforcement
) -> None:
    """A miss is invisible to a reader: they get the ID, exactly as before the round."""
    enforcement.backfill()
    enforcement.drain()

    assert resolved(listed, LISTED_USER) == {}


def test_a_batch_covers_several_users_in_one_job(
    listed: TestClient, enforcement: Enforcement, discord: FakeDiscord
) -> None:
    list_user(listed, OTHER_USER)
    list_user(listed, THIRD_USER)
    for user_id, name in ((LISTED_USER, "One"), (OTHER_USER, "Two"), (THIRD_USER, "Three")):
        discord.add_user(user_id, name)

    enforcement.backfill()
    enforcement.drain()

    assert resolved(listed, LISTED_USER, OTHER_USER, THIRD_USER) == {
        str(LISTED_USER): "One",
        str(OTHER_USER): "Two",
        str(THIRD_USER): "Three",
    }


def test_a_round_is_capped_and_the_rest_waits_for_the_next(
    listed: TestClient,
    enforcement: Enforcement,
    discord: FakeDiscord,
    settings: Settings,
) -> None:
    """The cap is what keeps a migrated backlog from becoming one enormous job. The
    limit rides on the payload, so the number in force is the one the round was queued
    with rather than whatever the setting says when it runs."""
    list_user(listed, OTHER_USER)
    discord.add_user(LISTED_USER, "One")
    discord.add_user(OTHER_USER, "Two")
    insert_job(settings, BACKFILL, {"limit": 1})

    enforcement.drain()

    assert len(discord.calls_of("fetch_user")) == 1
    # The second user is still owed a lookup, so tomorrow's round has something to do.
    assert enforcement.backfill() is True


# -- being a good citizen of Discord's rate limit ----------------------------


def test_a_rate_limit_ends_the_round_and_keeps_what_it_learned(
    listed: TestClient, enforcement: Enforcement, discord: FakeDiscord
) -> None:
    """Names are never urgent. Arguing with a rate limit would spend more than waiting a
    day costs, and what was already learned is kept either way."""
    list_user(listed, OTHER_USER)
    discord.add_user(LISTED_USER, "One")
    discord.add_user(OTHER_USER, "Two")
    discord.rate_limit_after(1)

    enforcement.backfill()
    enforcement.drain()

    # The same fake is the API's Discord, and reading a name still resolves the caller's
    # membership against it — so the limit has to be lifted before asking what landed.
    discord.reset_rate_limit()
    assert resolved(listed, LISTED_USER) == {str(LISTED_USER): "One"}
    assert resolved(listed, OTHER_USER) == {}


def test_a_rate_limited_round_is_not_a_failed_job(
    listed: TestClient, enforcement: Enforcement, discord: FakeDiscord, settings: Settings
) -> None:
    """It is a job that did less than it hoped, not one that could not run — so it is not
    retried, and the users it missed come back through the next round instead."""
    discord.rate_limit_after(0)

    enforcement.backfill()
    enforcement.drain()

    rounds = [job for job in jobs_of(settings) if job["kind"] == BACKFILL]
    assert [job["status"] for job in rounds] == ["done"]


# -- scheduling --------------------------------------------------------------


def test_a_round_queues_nothing_when_everybody_has_been_looked_up(
    listed: TestClient, enforcement: Enforcement, discord: FakeDiscord
) -> None:
    """Most days, after the first few weeks. A daily no-op job would be a year of rows to
    read past to find a real failure."""
    discord.add_user(LISTED_USER, "Nuisance")
    enforcement.backfill()
    enforcement.drain()

    assert enforcement.backfill() is False


def test_a_round_is_skipped_while_one_is_still_outstanding(
    listed: TestClient, enforcement: Enforcement
) -> None:
    """Otherwise a backlog longer than a day accumulates rounds it can never work off —
    the same accumulation the sweep guards against."""
    assert enforcement.backfill() is True

    assert enforcement.backfill() is False


def test_nothing_listed_means_nothing_to_do(pool: TestClient, enforcement: Enforcement) -> None:
    assert enforcement.backfill() is False
