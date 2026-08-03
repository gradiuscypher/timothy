"""Pools: owned by the management guild, readable by anyone Timothy shares a guild with."""

from fastapi.testclient import TestClient

from timothy_api.app import create_app
from timothy_api.settings import Settings
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    MANAGEMENT_ADMIN,
    MANAGEMENT_GUILD,
    MEMBER,
    OUTSIDER,
    POOL_MANAGER,
    POOL_MANAGER_ROLE,
    headers,
)


def test_a_pool_manager_creates_a_pool(registered: TestClient) -> None:
    response = registered.post(
        "/pools",
        json={"name": "spam", "description": "spammers"},
        headers=headers(POOL_MANAGER),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "spam"
    assert body["created_by"] == f"user:{POOL_MANAGER}"


def test_an_administrator_of_another_guild_may_not(registered: TestClient) -> None:
    """`ADMINISTRATOR` in a subscribing guild is authority over that guild, not over the
    shared lists (ADR 0001)."""
    response = registered.post("/pools", json={"name": "spam"}, headers=headers(GUILD_ADMIN))

    assert response.status_code == 403


def test_an_administrator_of_the_management_guild_may_not_either(
    registered: TestClient,
) -> None:
    """The whole of ADR 0012. Administering the guild the pools live in is running a
    Discord server; managing pools decides who every subscribing guild bans. An
    administrator who needs it grants themselves the role, which they always can — and
    that grant is then a visible act rather than a permission they already had."""
    response = registered.post(
        "/pools", json={"name": "spam"}, headers=headers(MANAGEMENT_ADMIN)
    )

    assert response.status_code == 403


def test_no_role_configured_closes_pool_management_for_everybody(
    settings: Settings, discord: FakeDiscord
) -> None:
    """It never falls back to the management guild's administrators. A fallback would
    make an unset variable mean "open to whoever it used to be open to", which is the
    one thing this must not mean."""
    app = create_app(
        settings.model_copy(update={"pool_manager_role_ids": frozenset()}),
        discord_port=discord,
    )

    with TestClient(app) as client:
        for actor in (POOL_MANAGER, MANAGEMENT_ADMIN):
            response = client.post("/pools", json={"name": "spam"}, headers=headers(actor))
            assert response.status_code == 403, actor


def test_a_second_configured_role_also_manages_pools(
    settings: Settings, discord: FakeDiscord
) -> None:
    """The setting is a set, so a deployment can hand pool management to more than one
    role without merging them in Discord."""
    other_role = 500_000_000_000_000_002
    someone = 200_000_000_000_000_007
    discord.add_member(MANAGEMENT_GUILD, someone, role_ids=frozenset({other_role}))
    app = create_app(
        settings.model_copy(
            update={"pool_manager_role_ids": frozenset({POOL_MANAGER_ROLE, other_role})}
        ),
        discord_port=discord,
    )

    with TestClient(app) as client:
        response = client.post("/pools", json={"name": "spam"}, headers=headers(someone))

    assert response.status_code == 201


def test_a_duplicate_name_is_a_conflict(pool: TestClient) -> None:
    response = pool.post("/pools", json={"name": "spam"}, headers=headers(POOL_MANAGER))

    assert response.status_code == 409


def test_a_member_may_read_pools(pool: TestClient) -> None:
    response = pool.get("/pools", headers=headers(MEMBER))

    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()] == ["spam"]


def test_someone_in_none_of_timothys_guilds_may_not_read(pool: TestClient) -> None:
    response = pool.get("/pools", headers=headers(OUTSIDER))

    assert response.status_code == 403


def test_naming_the_calling_guild_answers_in_one_call(
    pool: TestClient, discord: FakeDiscord
) -> None:
    """Reading pools needs membership of *any* guild Timothy is in, which is answered by
    asking Discord once per guild until one says yes.

    Discord paces that at about two calls a second. Across the migration's 123 guilds an
    unlucky order took 52 seconds — measured — against a bot that gives up after 2.5 and a
    Discord interaction that expires at 3. `/list_pools` is the only command a member with
    no administrator anywhere can reach, so it was the users with the least power who got
    the timeout.

    Counted for this caller alone: creating the pool in the fixture is a `fetch_member`
    too, now that pool authority is a role (ADR 0012).
    """
    response = pool.get(
        "/pools", headers=headers(MEMBER) | {"X-Timothy-From-Guild": str(GUILD)}
    )

    assert response.status_code == 200
    scanned = [call for call in discord.calls_of("fetch_member") if call.user_id == MEMBER]
    assert len(scanned) == 1


def test_the_calling_guild_is_a_hint_and_never_a_grant(
    pool: TestClient, discord: FakeDiscord
) -> None:
    """It reorders the scan and nothing else. Someone in none of Timothy's guilds who
    claims to be calling from one of them is still refused, because the answer still comes
    from Discord (ADR 0001)."""
    response = pool.get(
        "/pools", headers=headers(OUTSIDER) | {"X-Timothy-From-Guild": str(GUILD)}
    )

    assert response.status_code == 403


def test_an_unparseable_or_unknown_calling_guild_is_ignored(pool: TestClient) -> None:
    """A header naming nonsense, or a guild Timothy is not in, falls back to the plain
    scan rather than failing the request."""
    for value in ("not-a-snowflake", "999999999999999999", ""):
        response = pool.get("/pools", headers=headers(MEMBER) | {"X-Timothy-From-Guild": value})
        assert response.status_code == 200, value


def test_an_unknown_pool_is_a_404(pool: TestClient) -> None:
    assert pool.get("/pools/absent", headers=headers(MEMBER)).status_code == 404


def test_a_pool_can_be_renamed_without_touching_what_references_it(pool: TestClient) -> None:
    """The surrogate key exists for exactly this: listings and subscriptions carry the
    id, so a rename rewrites nothing."""
    created = pool.post(
        "/pools/spam/listings",
        json={"user_id": "300000000000000001", "reason": "spam"},
        headers=headers(POOL_MANAGER),
    ).json()

    renamed = pool.patch("/pools/spam", json={"name": "junk"}, headers=headers(POOL_MANAGER))

    assert renamed.status_code == 200
    assert renamed.json()["id"] == created["pool_id"]
    page = pool.get("/pools/junk/listings", headers=headers(POOL_MANAGER)).json()
    assert [entry["id"] for entry in page["listings"]] == [created["id"]]


def test_a_rename_onto_an_existing_name_is_a_conflict(pool: TestClient) -> None:
    pool.post("/pools", json={"name": "junk"}, headers=headers(POOL_MANAGER))

    response = pool.patch("/pools/spam", json={"name": "junk"}, headers=headers(POOL_MANAGER))

    assert response.status_code == 409


def test_a_description_can_be_changed_alone(pool: TestClient) -> None:
    response = pool.patch(
        "/pools/spam", json={"description": "now with detail"}, headers=headers(POOL_MANAGER)
    )

    assert response.status_code == 200
    assert response.json() == {
        **response.json(),
        "name": "spam",
        "description": "now with detail",
    }


def test_a_rename_that_leaves_the_description_alone_records_only_the_rename(
    pool: TestClient,
) -> None:
    response = pool.patch(
        "/pools/spam",
        json={"name": "junk", "description": "spammers"},
        headers=headers(POOL_MANAGER),
    )

    assert response.status_code == 200
    entry = pool.get("/audit-log", headers=headers(POOL_MANAGER)).json()[0]
    assert set(entry["detail"]["changed"]) == {"name"}


def test_deleting_a_pool_takes_its_listings_with_it(pool: TestClient) -> None:
    pool.post(
        "/pools/spam/listings",
        json={"user_id": "300000000000000001", "reason": "spam"},
        headers=headers(POOL_MANAGER),
    )

    assert pool.delete("/pools/spam", headers=headers(POOL_MANAGER)).status_code == 204
    assert pool.get("/pools/spam", headers=headers(POOL_MANAGER)).status_code == 404
    listings = pool.get(
        "/users/300000000000000001/listings", headers=headers(POOL_MANAGER)
    ).json()
    assert listings == []
