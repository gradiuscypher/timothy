"""Pools: owned by the management guild, readable by anyone Timothy shares a guild with."""

from fastapi.testclient import TestClient

from timothy_core.ports.fake import FakeDiscord

from .conftest import GUILD, GUILD_ADMIN, MEMBER, OUTSIDER, POOL_ADMIN, headers


def test_a_management_administrator_creates_a_pool(registered: TestClient) -> None:
    response = registered.post(
        "/pools",
        json={"name": "spam", "description": "spammers"},
        headers=headers(POOL_ADMIN),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "spam"
    assert body["created_by"] == f"user:{POOL_ADMIN}"


def test_an_administrator_of_another_guild_may_not(registered: TestClient) -> None:
    """`ADMINISTRATOR` in a subscribing guild is authority over that guild, not over the
    shared lists (ADR 0001)."""
    response = registered.post("/pools", json={"name": "spam"}, headers=headers(GUILD_ADMIN))

    assert response.status_code == 403


def test_a_duplicate_name_is_a_conflict(pool: TestClient) -> None:
    response = pool.post("/pools", json={"name": "spam"}, headers=headers(POOL_ADMIN))

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
    """
    response = pool.get(
        "/pools", headers=headers(MEMBER) | {"X-Timothy-From-Guild": str(GUILD)}
    )

    assert response.status_code == 200
    assert len(discord.calls_of("fetch_member")) == 1


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
        headers=headers(POOL_ADMIN),
    ).json()

    renamed = pool.patch("/pools/spam", json={"name": "junk"}, headers=headers(POOL_ADMIN))

    assert renamed.status_code == 200
    assert renamed.json()["id"] == created["pool_id"]
    page = pool.get("/pools/junk/listings", headers=headers(POOL_ADMIN)).json()
    assert [entry["id"] for entry in page["listings"]] == [created["id"]]


def test_a_rename_onto_an_existing_name_is_a_conflict(pool: TestClient) -> None:
    pool.post("/pools", json={"name": "junk"}, headers=headers(POOL_ADMIN))

    response = pool.patch("/pools/spam", json={"name": "junk"}, headers=headers(POOL_ADMIN))

    assert response.status_code == 409


def test_a_description_can_be_changed_alone(pool: TestClient) -> None:
    response = pool.patch(
        "/pools/spam", json={"description": "now with detail"}, headers=headers(POOL_ADMIN)
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
        headers=headers(POOL_ADMIN),
    )

    assert response.status_code == 200
    entry = pool.get("/audit-log", headers=headers(POOL_ADMIN)).json()[0]
    assert set(entry["detail"]["changed"]) == {"name"}


def test_deleting_a_pool_takes_its_listings_with_it(pool: TestClient) -> None:
    pool.post(
        "/pools/spam/listings",
        json={"user_id": "300000000000000001", "reason": "spam"},
        headers=headers(POOL_ADMIN),
    )

    assert pool.delete("/pools/spam", headers=headers(POOL_ADMIN)).status_code == 204
    assert pool.get("/pools/spam", headers=headers(POOL_ADMIN)).status_code == 404
    listings = pool.get(
        "/users/300000000000000001/listings", headers=headers(POOL_ADMIN)
    ).json()
    assert listings == []
