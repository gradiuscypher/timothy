"""Subscriptions: what a guild has asked Timothy to enforce, and at what level."""

from collections.abc import Callable

from fastapi.testclient import TestClient

from timothy_api.jobs import JobKind

from .conftest import GUILD, GUILD_ADMIN, MEMBER, POOL_MANAGER, headers

Enqueued = Callable[[], list[tuple[str, dict[str, int]]]]


def test_a_guild_administrator_subscribes(pool: TestClient) -> None:
    response = pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 200
    assert response.json()["level"] == "ban"
    assert response.json()["pool_name"] == "spam"


def test_the_pools_owner_has_no_say_over_another_guild(pool: TestClient) -> None:
    """Authority over the shared lists is not authority over who enforces them
    (ADR 0001)."""
    response = pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(POOL_MANAGER),
    )

    assert response.status_code == 403


def test_a_member_may_not_subscribe(pool: TestClient) -> None:
    response = pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(MEMBER),
    )

    assert response.status_code == 403


def test_subscribing_to_an_unknown_pool_is_a_404(pool: TestClient) -> None:
    response = pool.put(
        f"/guilds/{GUILD}/subscriptions/absent",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 404


def test_only_ban_and_warn_are_levels(pool: TestClient) -> None:
    response = pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "kick"},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 422


def test_subscribing_enqueues_enforcement(pool: TestClient, enqueued: Enqueued) -> None:
    created = pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "warn"},
        headers=headers(GUILD_ADMIN),
    ).json()

    assert enqueued() == [
        (
            JobKind.ENFORCE_SUBSCRIPTION.value,
            {"guild_id": GUILD, "pool_id": created["pool_id"]},
        )
    ]


def test_raising_warn_to_ban_enqueues_enforcement(pool: TestClient, enqueued: Enqueued) -> None:
    """PLAN.md: a guild that switches `warn` to `ban` has its members picked up on the
    next pass."""
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "warn"},
        headers=headers(GUILD_ADMIN),
    )

    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )

    assert len(enqueued()) == 2


def test_lowering_ban_to_warn_enqueues_nothing(pool: TestClient, enqueued: Enqueued) -> None:
    """Asking to stop banning from now on is not asking to undo what has already been
    done — that is what `revert` on the unsubscribe is for."""
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )

    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "warn"},
        headers=headers(GUILD_ADMIN),
    )

    assert len(enqueued()) == 1


def test_setting_the_same_level_again_enqueues_nothing(
    pool: TestClient, enqueued: Enqueued
) -> None:
    for _ in range(2):
        pool.put(
            f"/guilds/{GUILD}/subscriptions/spam",
            json={"level": "ban"},
            headers=headers(GUILD_ADMIN),
        )

    assert len(enqueued()) == 1


def test_unsubscribing_reverts_nothing_unless_asked(
    pool: TestClient, enqueued: Enqueued
) -> None:
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )

    response = pool.delete(f"/guilds/{GUILD}/subscriptions/spam", headers=headers(GUILD_ADMIN))

    assert response.status_code == 204
    assert [kind for kind, _ in enqueued()] == [JobKind.ENFORCE_SUBSCRIPTION.value]


def test_unsubscribing_with_revert_enqueues_one(pool: TestClient, enqueued: Enqueued) -> None:
    created = pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    ).json()

    pool.delete(f"/guilds/{GUILD}/subscriptions/spam?revert=true", headers=headers(GUILD_ADMIN))

    assert enqueued()[-1] == (
        JobKind.REVERT_SUBSCRIPTION.value,
        {"guild_id": GUILD, "pool_id": created["pool_id"]},
    )


def test_unsubscribing_from_something_not_subscribed_is_a_404(pool: TestClient) -> None:
    response = pool.delete(f"/guilds/{GUILD}/subscriptions/spam", headers=headers(GUILD_ADMIN))

    assert response.status_code == 404


def test_deleting_a_pool_takes_its_subscriptions_with_it(pool: TestClient) -> None:
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )

    pool.delete("/pools/spam", headers=headers(POOL_MANAGER))

    assert pool.get(f"/guilds/{GUILD}/subscriptions", headers=headers(GUILD_ADMIN)).json() == []


def test_deleting_a_pool_with_revert_enqueues_one(pool: TestClient, enqueued: Enqueued) -> None:
    """The pool row goes; the enforcement outcomes it caused do not, which is what leaves
    the revert able to find the bans afterwards (ADR 0005)."""
    created = pool.get("/pools/spam", headers=headers(POOL_MANAGER)).json()

    pool.delete("/pools/spam?revert=true", headers=headers(POOL_MANAGER))

    assert enqueued()[-1] == (JobKind.REVERT_POOL.value, {"pool_id": created["id"]})
