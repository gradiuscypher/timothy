"""Listings, and the enforcement they imply but do not perform."""

from collections.abc import Callable

from fastapi.testclient import TestClient

from timothy_api.jobs import JobKind
from timothy_core.ports.fake import FakeDiscord

from .conftest import GUILD_ADMIN, LISTED_USER, MEMBER, POOL_ADMIN, headers

Enqueued = Callable[[], list[tuple[str, dict[str, int]]]]


def test_a_listing_is_created_by_the_management_guild(pool: TestClient) -> None:
    response = pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "raided three servers"},
        headers=headers(POOL_ADMIN),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == str(LISTED_USER)
    assert body["pool_name"] == "spam"
    assert body["reason"] == "raided three servers"


def test_a_guild_administrator_may_not_list(pool: TestClient) -> None:
    response = pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "no"},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 403


def test_listing_a_user_enqueues_enforcement(pool: TestClient, enqueued: Enqueued) -> None:
    """ADR 0004: creating a listing no longer waits for the hourly sweep."""
    created = pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_ADMIN),
    ).json()

    assert enqueued() == [(JobKind.ENFORCE_LISTING.value, {"listing_id": created["id"]})]


def test_a_listing_bans_nobody_by_itself(pool: TestClient, discord: FakeDiscord) -> None:
    """An assertion, not an action — CONTEXT.md. The workers that act on it are phase 3,
    and until they exist nothing should have reached Discord."""
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_ADMIN),
    )

    assert discord.calls_of("ban") == []


def test_the_same_user_cannot_be_listed_twice_on_a_pool(pool: TestClient) -> None:
    body = {"user_id": str(LISTED_USER), "reason": "spam"}
    pool.post("/pools/spam/listings", json=body, headers=headers(POOL_ADMIN))

    response = pool.post("/pools/spam/listings", json=body, headers=headers(POOL_ADMIN))

    assert response.status_code == 409


def test_the_same_user_can_be_listed_on_two_pools(pool: TestClient) -> None:
    pool.post("/pools", json={"name": "raiders"}, headers=headers(POOL_ADMIN))
    body = {"user_id": str(LISTED_USER), "reason": "spam"}

    pool.post("/pools/spam/listings", json=body, headers=headers(POOL_ADMIN))
    second = pool.post("/pools/raiders/listings", json=body, headers=headers(POOL_ADMIN))

    assert second.status_code == 201


def test_a_listing_on_an_unknown_pool_is_a_404(pool: TestClient) -> None:
    response = pool.post(
        "/pools/absent/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_ADMIN),
    )

    assert response.status_code == 404


def test_why_a_user_is_listed_spans_every_pool(pool: TestClient) -> None:
    pool.post("/pools", json={"name": "raiders"}, headers=headers(POOL_ADMIN))
    for name, reason in (("spam", "spamming"), ("raiders", "raiding")):
        pool.post(
            f"/pools/{name}/listings",
            json={"user_id": str(LISTED_USER), "reason": reason},
            headers=headers(POOL_ADMIN),
        )

    response = pool.get(f"/users/{LISTED_USER}/listings", headers=headers(MEMBER))

    assert response.status_code == 200
    assert [(entry["pool_name"], entry["reason"]) for entry in response.json()] == [
        ("raiders", "raiding"),
        ("spam", "spamming"),
    ]


def test_removing_a_listing_reverts_nothing_unless_asked(
    pool: TestClient, enqueued: Enqueued
) -> None:
    """ADR 0005: `revert` is off by default, because a ban Timothy cannot attribute to
    itself is the guild's own and is never touched."""
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_ADMIN),
    )

    response = pool.delete(f"/pools/spam/listings/{LISTED_USER}", headers=headers(POOL_ADMIN))

    assert response.status_code == 204
    assert [kind for kind, _ in enqueued()] == [JobKind.ENFORCE_LISTING.value]


def test_removing_a_listing_with_revert_enqueues_one(
    pool: TestClient, enqueued: Enqueued
) -> None:
    created = pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_ADMIN),
    ).json()

    pool.delete(f"/pools/spam/listings/{LISTED_USER}?revert=true", headers=headers(POOL_ADMIN))

    assert enqueued()[-1] == (
        JobKind.REVERT_LISTING.value,
        {"pool_id": created["pool_id"], "user_id": LISTED_USER},
    )


def test_removing_a_listing_that_is_not_there_is_a_404(pool: TestClient) -> None:
    response = pool.delete(f"/pools/spam/listings/{LISTED_USER}", headers=headers(POOL_ADMIN))

    assert response.status_code == 404


def test_a_snowflake_survives_the_round_trip_exactly(pool: TestClient) -> None:
    """1.4e18 is well past where a JSON number stops being exact."""
    big = 1_400_000_000_000_000_001
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(big), "reason": "spam"},
        headers=headers(POOL_ADMIN),
    )

    page = pool.get("/pools/spam/listings", headers=headers(POOL_ADMIN)).json()

    assert page["listings"][0]["user_id"] == str(big)
