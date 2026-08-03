"""Every mutation leaves a line, and the line says who.

PLAN.md's backlog wanted "action audit logs" and this is it. What matters is that no
mutation is silent and that Timothy's own actions are distinguishable from a person's —
the old bot attributed its own work to user ID `"0"`.
"""

from fastapi.testclient import TestClient

from timothy_api.audit import AuditAction

from .conftest import (
    CHANNEL,
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    MEMBER,
    POOL_MANAGER,
    headers,
)

ENFORCEMENT_ACTIONS = {
    action.value for action in AuditAction if action.value.startswith("enforcement.")
}
"""What the worker writes, not what a request does. Nobody can provoke these through the
CRUD surface — enforcement is queued, and the tests that drive it live elsewhere."""


def actions(client: TestClient) -> list[str]:
    entries = client.get("/audit-log", headers=headers(POOL_MANAGER)).json()
    return [entry["action"] for entry in entries]


def test_only_the_management_guild_reads_the_log(registered: TestClient) -> None:
    assert registered.get("/audit-log", headers=headers(GUILD_ADMIN)).status_code == 403
    assert registered.get("/audit-log", headers=headers(MEMBER)).status_code == 403
    assert registered.get("/audit-log", headers=headers(POOL_MANAGER)).status_code == 200


def test_creating_a_pool_is_recorded(pool: TestClient) -> None:
    entries = pool.get("/audit-log", headers=headers(POOL_MANAGER)).json()

    assert entries[0]["action"] == AuditAction.POOL_CREATE.value
    assert entries[0]["target"] == "pool:spam"
    assert entries[0]["actor"] == f"user:{POOL_MANAGER}"


def test_timothys_own_work_is_attributed_to_timothy(client: TestClient) -> None:
    """Not to a magic user ID indistinguishable from a real person (ADR 0006)."""
    client.post("/pools", json={"name": "global"}, headers=headers(POOL_MANAGER))
    client.put(f"/guilds/{GUILD}", headers=headers("system"))

    entries = client.get("/audit-log", headers=headers(POOL_MANAGER)).json()
    subscription = next(
        entry for entry in entries if entry["action"] == AuditAction.SUBSCRIPTION_SET.value
    )

    assert subscription["actor"] == "system"
    assert subscription["detail"]["reason"] == "joined"


def test_every_mutation_leaves_a_line(pool: TestClient) -> None:
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_MANAGER),
    )
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    pool.put(f"/guilds/{GUILD}/exceptions/{LISTED_USER}", json={}, headers=headers(GUILD_ADMIN))
    pool.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )
    pool.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": True}, headers=headers(GUILD_ADMIN)
    )
    pool.patch("/pools/spam", json={"description": "x"}, headers=headers(POOL_MANAGER))
    pool.delete(f"/guilds/{GUILD}/exceptions/{LISTED_USER}", headers=headers(GUILD_ADMIN))
    pool.delete(f"/pools/spam/listings/{LISTED_USER}", headers=headers(POOL_MANAGER))
    pool.delete(f"/guilds/{GUILD}/notification-channel", headers=headers(GUILD_ADMIN))
    pool.delete(f"/guilds/{GUILD}/subscriptions/spam", headers=headers(GUILD_ADMIN))
    pool.delete("/pools/spam", headers=headers(POOL_MANAGER))
    pool.delete(f"/guilds/{GUILD}", headers=headers("system"))

    assert set(actions(pool)) == {action.value for action in AuditAction} - ENFORCEMENT_ACTIONS


def test_a_refused_call_records_nothing(pool: TestClient) -> None:
    """The log is what happened, not what was attempted."""
    before = len(actions(pool))

    pool.post("/pools", json={"name": "denied"}, headers=headers(GUILD_ADMIN))

    assert len(actions(pool)) == before


def test_a_failed_mutation_records_nothing(pool: TestClient) -> None:
    """The audit row shares the mutation's transaction, so a 409 rolls it back with
    everything else."""
    before = len(actions(pool))

    assert (
        pool.post("/pools", json={"name": "spam"}, headers=headers(POOL_MANAGER)).status_code
        == 409
    )

    assert len(actions(pool)) == before


def test_a_rename_records_what_it_changed(pool: TestClient) -> None:
    pool.patch("/pools/spam", json={"name": "junk"}, headers=headers(POOL_MANAGER))

    entries = pool.get("/audit-log", headers=headers(POOL_MANAGER)).json()

    assert entries[0]["detail"]["changed"]["name"] == {"from": "spam", "to": "junk"}


def test_the_newest_entry_comes_first(pool: TestClient) -> None:
    pool.post("/pools", json={"name": "raiders"}, headers=headers(POOL_MANAGER))

    entries = pool.get("/audit-log", headers=headers(POOL_MANAGER)).json()

    assert entries[0]["target"] == "pool:raiders"
    assert entries[1]["target"] == "pool:spam"


def test_the_log_pages_by_cursor(pool: TestClient) -> None:
    """By id rather than offset: the table only grows at one end, so an offset would
    shift under a reader as new rows arrive."""
    for name in ("a", "b", "c"):
        pool.post("/pools", json={"name": name}, headers=headers(POOL_MANAGER))

    first = pool.get("/audit-log?limit=2", headers=headers(POOL_MANAGER)).json()
    second = pool.get(
        f"/audit-log?limit=2&before_id={first[-1]['id']}", headers=headers(POOL_MANAGER)
    ).json()

    assert len(first) == 2
    assert all(entry["id"] < first[-1]["id"] for entry in second)


def test_the_log_filters_by_action(pool: TestClient) -> None:
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_MANAGER),
    )

    entries = pool.get(
        f"/audit-log?action={AuditAction.LISTING_CREATE.value}", headers=headers(POOL_MANAGER)
    ).json()

    assert [entry["action"] for entry in entries] == [AuditAction.LISTING_CREATE.value]


def test_the_log_finds_a_user_wherever_they_are_recorded(pool: TestClient) -> None:
    """The point of the box. A user ID is the actor on one line and the target on
    another, and somebody asking "what happened to this person" wants both."""
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_MANAGER),
    )

    entries = pool.get(f"/audit-log?q={LISTED_USER}", headers=headers(POOL_MANAGER)).json()

    assert [entry["action"] for entry in entries] == [AuditAction.LISTING_CREATE.value]
    assert entries[0]["target"] == f"listing:spam/{LISTED_USER}"


def test_the_log_searches_the_detail(pool: TestClient) -> None:
    """`detail` is JSON and it is where the reason, the level and the old name live.
    Without it the box would miss the thing most worth finding."""
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "coordinated raid"},
        headers=headers(POOL_MANAGER),
    )

    entries = pool.get("/audit-log?q=coordinated", headers=headers(POOL_MANAGER)).json()

    assert [entry["action"] for entry in entries] == [AuditAction.LISTING_CREATE.value]


def test_the_search_and_the_action_filter_narrow_together(pool: TestClient) -> None:
    """Not one overriding the other: the dropdown says which kind of line, the box says
    which subject, and answering "when was this user listed" needs both."""
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_MANAGER),
    )

    entries = pool.get(
        f"/audit-log?q={LISTED_USER}&action={AuditAction.POOL_CREATE.value}",
        headers=headers(POOL_MANAGER),
    ).json()

    assert entries == []


def test_a_wildcard_in_the_search_is_a_character_and_not_a_pattern(pool: TestClient) -> None:
    """Somebody searching for `100%` is searching for a string. An unescaped `%` would
    match every row in the table and look like the search had been ignored."""
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_MANAGER),
    )

    # `%25` is a literal per cent sign once the URL is decoded.
    entries = pool.get("/audit-log?q=%25", headers=headers(POOL_MANAGER)).json()

    assert entries == []


def test_an_absurd_page_size_is_refused(pool: TestClient) -> None:
    assert pool.get("/audit-log?limit=5000", headers=headers(POOL_MANAGER)).status_code == 422
