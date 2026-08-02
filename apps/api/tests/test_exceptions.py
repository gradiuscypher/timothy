"""Exceptions: a guild's declaration that a user is never to be banned by Timothy there."""

from collections.abc import Callable

from fastapi.testclient import TestClient

from timothy_api.jobs import JobKind

from .conftest import GUILD, GUILD_ADMIN, LISTED_USER, MEMBER, POOL_ADMIN, headers

Enqueued = Callable[[], list[tuple[str, dict[str, int]]]]


def test_a_guild_administrator_vouches_for_a_user(registered: TestClient) -> None:
    response = registered.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}",
        json={"reason": "our own moderator"},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == str(LISTED_USER)
    assert response.json()["reason"] == "our own moderator"


def test_an_exception_is_guild_wide_not_per_pool(registered: TestClient) -> None:
    """ADR 0006 kept this: it matches how moderators think — "I vouch for this person in
    my server" — and there is deliberately no pool in the route."""
    registered.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN)
    )

    listed = registered.get(f"/guilds/{GUILD}/exceptions", headers=headers(GUILD_ADMIN)).json()

    assert set(listed[0]) == {"guild_id", "user_id", "reason", "created_by", "created_at"}


def test_the_pools_owner_may_not_vouch_in_someone_elses_guild(
    registered: TestClient,
) -> None:
    response = registered.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(POOL_ADMIN)
    )

    assert response.status_code == 403


def test_a_member_may_not_vouch(registered: TestClient) -> None:
    response = registered.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(MEMBER)
    )

    assert response.status_code == 403


def test_vouching_twice_is_a_conflict(registered: TestClient) -> None:
    registered.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN)
    )

    response = registered.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 409


def test_creating_an_exception_enqueues_nothing(
    registered: TestClient, enqueued: Enqueued
) -> None:
    """Whether an exception should lift a ban Timothy has already issued is a revert, and
    reverts are ADR 0005's territory — an open question for phase 3, not one to settle by
    implication here."""
    registered.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN)
    )

    assert enqueued() == []


def test_withdrawing_a_vouch_lets_enforcement_look_again(
    registered: TestClient, enqueued: Enqueued
) -> None:
    registered.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN)
    )

    response = registered.delete(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 204
    assert enqueued() == [
        (JobKind.ENFORCE_GUILD_USER.value, {"guild_id": GUILD, "user_id": LISTED_USER})
    ]


def test_withdrawing_a_vouch_that_is_not_there_is_a_404(registered: TestClient) -> None:
    response = registered.delete(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 404


def test_exceptions_in_an_unregistered_guild_are_a_404(client: TestClient) -> None:
    response = client.get(f"/guilds/{GUILD}/exceptions", headers=headers(GUILD_ADMIN))

    assert response.status_code == 404


def test_leaving_a_guild_forgets_its_exceptions(registered: TestClient) -> None:
    registered.put(
        f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN)
    )
    registered.delete(f"/guilds/{GUILD}", headers=headers("system"))
    registered.put(f"/guilds/{GUILD}", headers=headers("system"))

    listed = registered.get(f"/guilds/{GUILD}/exceptions", headers=headers(GUILD_ADMIN))

    assert listed.json() == []
