"""Paginated search over listings, and bulk operations. The web-only half of phase 6.

None of this has a slash command behind it. Pagination exists because `global` carries
thousands of listings after the migration, and bulk exists because pasting three hundred
user IDs into a Discord text box is not a thing anybody should be asked to do.
"""

from collections.abc import Callable
from typing import Any

import httpx2
from fastapi.testclient import TestClient

from timothy_core.ports.fake import FakeDiscord

from .conftest import GUILD, GUILD_ADMIN, LISTED_USER, POOL_MANAGER, Enforcement, headers

Enqueued = Callable[[], list[tuple[str, dict[str, int]]]]

REASON = "raid of 2026-08-01"


def _add(client: TestClient, user_ids: list[int], *, reason: str = REASON) -> httpx2.Response:
    return client.post(
        "/pools/spam/listings/bulk",
        json={"reason": reason, "user_ids": [str(user_id) for user_id in user_ids]},
        headers=headers(POOL_MANAGER),
    )


def _page(client: TestClient, query: str = "") -> Any:  # noqa: ANN401 — the page as JSON
    return client.get(f"/pools/spam/listings{query}", headers=headers(POOL_MANAGER)).json()


def _name(client: TestClient, *, user_id: int, username: str) -> None:
    """Teach Timothy what a user is called, the way the bot does — see `test_usernames`."""
    response = client.post(
        "/events/member-join",
        json={"guild_id": str(GUILD), "user_id": str(user_id), "username": username},
        headers=headers("system"),
    )
    assert response.status_code == 202, response.text


# -- pagination ------------------------------------------------------------------------


def test_a_page_says_how_many_there_are_in_total(pool: TestClient) -> None:
    """The count is of everything matching, not of the page — it is what goes above the
    table, and a number that changed as you paged would be worse than none."""
    _add(pool, list(range(1_000, 1_005)))

    page = _page(pool, "?limit=2")

    assert len(page["listings"]) == 2
    assert page["total"] == 5


def test_the_cursor_walks_every_listing_exactly_once(pool: TestClient) -> None:
    _add(pool, list(range(1_000, 1_007)))

    seen: list[str] = []
    cursor: int | None = None
    for _ in range(10):
        query = "?limit=3" + (f"&after_id={cursor}" if cursor else "")
        page = _page(pool, query)
        seen.extend(entry["user_id"] for entry in page["listings"])
        cursor = page["next_after_id"]
        if cursor is None:
            break

    assert seen == [str(user_id) for user_id in range(1_000, 1_007)]


def test_the_last_page_offers_no_cursor(pool: TestClient) -> None:
    _add(pool, [1_000, 1_001])

    assert _page(pool, "?limit=50")["next_after_id"] is None


def test_a_full_last_page_offers_a_cursor_that_comes_back_empty(pool: TestClient) -> None:
    """A page exactly the size of the limit cannot be told apart from a full one without
    asking again. Offering the cursor is the honest answer; the next page is empty."""
    _add(pool, [1_000, 1_001])

    page = _page(pool, "?limit=2")
    assert page["next_after_id"] is not None

    assert _page(pool, f"?limit=2&after_id={page['next_after_id']}")["listings"] == []


# -- search ----------------------------------------------------------------------------


def test_search_matches_the_reason(pool: TestClient) -> None:
    _add(pool, [1_000], reason="ban evasion")
    _add(pool, [1_001], reason="raiding")

    page = _page(pool, "?q=evasion")

    assert [entry["user_id"] for entry in page["listings"]] == ["1000"]
    assert page["total"] == 1


def test_search_ignores_case(pool: TestClient) -> None:
    _add(pool, [1_000], reason="Ban Evasion")

    assert _page(pool, "?q=ban evasion")["total"] == 1


def test_search_matches_part_of_a_user_id(pool: TestClient) -> None:
    """Somebody reading a screenshot types the digits they can make out, not all
    eighteen."""
    _add(pool, [LISTED_USER])

    assert _page(pool, f"?q={str(LISTED_USER)[4:12]}")["total"] == 1


def test_a_search_for_a_wildcard_is_a_search_for_that_character(pool: TestClient) -> None:
    """A moderator typing `_` is searching for an underscore, not writing a pattern.
    Unescaped, `_` is LIKE's "any one character" and would match every listing there is."""
    _add(pool, [1_000], reason="alt_account")
    _add(pool, [1_001], reason="altXaccount")

    page = _page(pool, "?q=alt_account")

    assert [entry["user_id"] for entry in page["listings"]] == ["1000"]


def test_search_matches_the_name_timothy_has_for_a_listed_user(pool: TestClient) -> None:
    """The handle a moderator has on a snowflake is what the person is called. A box that
    found the reason but not the name would send them to the lookup page and back."""
    _add(pool, [1_000], reason="raiding")
    _add(pool, [1_001], reason="raiding")
    _name(pool, user_id=1_000, username="Nuisance")

    page = _page(pool, "?q=nuisance")

    assert [entry["user_id"] for entry in page["listings"]] == ["1000"]
    assert page["total"] == 1


def test_a_listing_whose_user_has_no_name_is_still_on_the_page(pool: TestClient) -> None:
    """The join is outer for this reason: most of the migrated thousands have never been
    seen, and an inner one would shrink a pool to the users Timothy happens to know."""
    _add(pool, [1_000, 1_001])
    _name(pool, user_id=1_000, username="Nuisance")

    page = _page(pool)

    assert [entry["user_id"] for entry in page["listings"]] == ["1000", "1001"]
    assert page["total"] == 2


def test_search_and_pagination_compose(pool: TestClient) -> None:
    _add(pool, [1_000, 1_001, 1_002], reason="raiding")
    _add(pool, [2_000], reason="something else")

    page = _page(pool, "?q=raiding&limit=2")

    assert page["total"] == 3
    assert len(page["listings"]) == 2


# -- bulk create -----------------------------------------------------------------------


def test_bulk_creates_every_listing_and_says_so(pool: TestClient) -> None:
    response = _add(pool, [1_000, 1_001, 1_002])

    assert response.status_code == 200
    assert response.json() == {"applied": ["1000", "1001", "1002"], "skipped": []}
    assert _page(pool)["total"] == 3


def test_bulk_skips_what_is_already_listed_rather_than_failing(pool: TestClient) -> None:
    """Somebody who asked for five hundred and gave three that were already there wants
    the four hundred and ninety-seven, and wants to be told about the three."""
    _add(pool, [1_000])

    result = _add(pool, [1_000, 1_001]).json()

    assert result == {"applied": ["1001"], "skipped": ["1000"]}


def test_bulk_collapses_repeats_within_one_request(pool: TestClient) -> None:
    """Pasting a list with duplicates in it does what the person meant."""
    result = _add(pool, [1_000, 1_000, 1_001]).json()

    assert result == {"applied": ["1000", "1001"], "skipped": []}


def test_bulk_enqueues_enforcement_for_each_new_listing(
    pool: TestClient, enqueued: Enqueued
) -> None:
    _add(pool, [1_000, 1_001])

    assert [kind for kind, _ in enqueued()] == ["enforce_listing", "enforce_listing"]


def test_bulk_writes_one_audit_row_per_listing(pool: TestClient) -> None:
    """The log has to keep answering "why is this user listed and who did it". A single
    summary row for three hundred listings cannot."""
    _add(pool, [1_000, 1_001])

    entries = pool.get("/audit-log?action=listing.create", headers=headers(POOL_MANAGER)).json()

    assert [entry["target"] for entry in entries] == [
        "listing:spam/1001",
        "listing:spam/1000",
    ]
    assert all(entry["detail"]["bulk"] is True for entry in entries)


def test_a_single_listing_is_not_marked_as_bulk(pool: TestClient) -> None:
    pool.post(
        "/pools/spam/listings",
        json={"user_id": "1000", "reason": REASON},
        headers=headers(POOL_MANAGER),
    )

    entry = pool.get("/audit-log", headers=headers(POOL_MANAGER)).json()[0]

    assert "bulk" not in entry["detail"]


def test_bulk_is_refused_to_anyone_who_does_not_own_pools(pool: TestClient) -> None:
    response = pool.post(
        "/pools/spam/listings/bulk",
        json={"reason": REASON, "user_ids": ["1000"]},
        headers=headers(999_000_000_000_000_001),
    )

    assert response.status_code == 403


def test_bulk_is_bounded(pool: TestClient) -> None:
    """Every entry becomes enforcement across every subscribing guild. This is the
    operation that most deserves to be reviewed before it is sent."""
    response = _add(pool, list(range(1_000, 1_501)))

    assert response.status_code == 422


def test_bulk_needs_at_least_one_user(pool: TestClient) -> None:
    assert _add(pool, []).status_code == 422


def test_bulk_on_a_pool_that_does_not_exist_is_a_404(pool: TestClient) -> None:
    response = pool.post(
        "/pools/nowhere/listings/bulk",
        json={"reason": REASON, "user_ids": ["1000"]},
        headers=headers(POOL_MANAGER),
    )

    assert response.status_code == 404


# -- bulk delete -----------------------------------------------------------------------


def _remove(
    client: TestClient, user_ids: list[int], *, revert: bool = False
) -> httpx2.Response:
    return client.post(
        f"/pools/spam/listings/bulk-delete?revert={str(revert).lower()}",
        json={"user_ids": [str(user_id) for user_id in user_ids]},
        headers=headers(POOL_MANAGER),
    )


def test_bulk_delete_removes_every_listing_named(pool: TestClient) -> None:
    _add(pool, [1_000, 1_001, 1_002])

    result = _remove(pool, [1_000, 1_002]).json()

    assert result == {"applied": ["1000", "1002"], "skipped": []}
    assert [entry["user_id"] for entry in _page(pool)["listings"]] == ["1001"]


def test_bulk_delete_skips_what_was_never_listed(pool: TestClient) -> None:
    _add(pool, [1_000])

    assert _remove(pool, [1_000, 9_999]).json()["skipped"] == ["9999"]


def test_bulk_delete_leaves_the_bans_alone_by_default(
    pool: TestClient, enqueued: Enqueued
) -> None:
    """Reverting is only ever safe for bans Timothy has a recorded outcome for, so it is
    asked for rather than assumed (ADR 0005)."""
    _add(pool, [1_000])
    _remove(pool, [1_000])

    assert [kind for kind, _ in enqueued()] == ["enforce_listing"]


def test_bulk_delete_can_lift_the_bans_it_leaves_unjustified(
    pool: TestClient, enqueued: Enqueued
) -> None:
    _add(pool, [1_000, 1_001])
    _remove(pool, [1_000, 1_001], revert=True)

    assert [kind for kind, _ in enqueued()][-2:] == ["revert_listing", "revert_listing"]


def test_a_bulk_listing_really_does_ban_everybody_it_names(
    pool: TestClient, enforcement: Enforcement, discord: FakeDiscord
) -> None:
    """Not a queue assertion: the whole way through, into Discord."""
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    for user_id in (1_000, 1_001):
        discord.add_member(GUILD, user_id)

    _add(pool, [1_000, 1_001])
    enforcement.drain()

    assert discord.is_banned(GUILD, 1_000)
    assert discord.is_banned(GUILD, 1_001)
