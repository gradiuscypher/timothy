"""Telling a guild's administrators why Timothy cannot ban there.

The cases worth writing down are the ones where a plausible implementation is wrong in a
way nobody notices: a role *level* with Timothy reading as fine, a member count of zero
standing in for "we could not count", and an all-clear for a guild nothing has ever
looked at.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import (
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    MEMBER,
    OTHER_GUILD,
    POOL_MANAGER,
    Enforcement,
    headers,
)
from timothy_core.ports.discord import ForbiddenError
from timothy_core.ports.fake import FakeDiscord

TIMOTHY_TOP = 5

MODERATOR_ROLE = 600_000_000_000_000_001
ADMIN_ROLE = 600_000_000_000_000_002
MEMBER_ROLE = 600_000_000_000_000_003
BOOSTER_ROLE = 600_000_000_000_000_004

GUILD_OWNER = 200_000_000_000_000_009


def report(**overrides: object) -> dict[str, Any]:
    """A snapshot in the shape the bot sends.

    `MODERATOR_ROLE` sits at exactly Timothy's own position: Discord's hierarchy is a
    strict inequality, so it is unbannable and looks fine in Discord's own role list.
    """
    return {
        "can_ban": True,
        "is_administrator": False,
        "top_role_position": TIMOTHY_TOP,
        "top_role_name": "Timothy",
        "owner_id": str(GUILD_OWNER),
        "member_counts_complete": True,
        "roles": [
            {
                "role_id": str(MEMBER_ROLE),
                "name": "member",
                "position": 1,
                "member_count": 4000,
            },
            {
                "role_id": str(MODERATOR_ROLE),
                "name": "moderator",
                "position": TIMOTHY_TOP,
                "member_count": 12,
            },
            {"role_id": str(ADMIN_ROLE), "name": "admin", "position": 9, "member_count": 3},
        ],
        **overrides,
    }


def observe(client: TestClient, guild_id: int = GUILD, **overrides: object) -> dict[str, Any]:
    """Have the bot report on a guild, as it does on its own timer."""
    response = client.put(
        f"/guilds/{guild_id}/diagnostics",
        json=report(**overrides),
        headers=headers("system"),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def read(client: TestClient, guild_id: int = GUILD) -> dict[str, Any]:
    response = client.get(f"/guilds/{guild_id}/diagnostics", headers=headers(GUILD_ADMIN))
    assert response.status_code == 200, response.text
    return dict(response.json())


# -- reporting ---------------------------------------------------------------


def test_a_guild_nobody_has_looked_at_is_not_a_guild_that_is_fine(
    registered: TestClient,
) -> None:
    """404, not a row of cheerful defaults. An unmeasured all-clear is the worst answer."""
    response = registered.get(f"/guilds/{GUILD}/diagnostics", headers=headers(GUILD_ADMIN))

    assert response.status_code == 404
    assert "has not reported" in response.json()["detail"]


def test_reporting_on_a_guild_timothy_is_not_in_is_refused(registered: TestClient) -> None:
    unknown = 100_000_000_000_000_099
    response = registered.put(
        f"/guilds/{unknown}/diagnostics", json=report(), headers=headers("system")
    )

    assert response.status_code == 404


def test_only_the_bot_may_report(registered: TestClient) -> None:
    """`SYSTEM` is refused everything a person owns, and the reverse (ADR 0001)."""
    response = registered.put(
        f"/guilds/{GUILD}/diagnostics", json=report(), headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 403


def test_a_later_report_replaces_the_roles_rather_than_merging_them(
    registered: TestClient,
) -> None:
    """A role deleted in Discord must stop being reported, not linger as a blocker
    nobody can find in their own settings."""
    observe(registered)
    observe(
        registered,
        roles=[{"role_id": str(ADMIN_ROLE), "name": "admin", "position": 9, "member_count": 3}],
    )

    names = [role["name"] for role in read(registered)["unbannable_roles"]]
    assert names == ["admin"]


# -- what is out of reach ----------------------------------------------------


def test_a_role_level_with_timothy_is_reported_unbannable(registered: TestClient) -> None:
    """The off-by-one this whole feature exists to catch."""
    observe(registered)

    unbannable = read(registered)["unbannable_roles"]

    assert [role["name"] for role in unbannable] == ["admin", "moderator"]


def test_roles_below_timothy_are_not_reported(registered: TestClient) -> None:
    observe(registered)

    assert "member" not in {role["name"] for role in read(registered)["unbannable_roles"]}


def test_the_ceiling_sums_the_unbannable_roles(registered: TestClient) -> None:
    observe(registered)

    assert read(registered)["unbannable_members"] == 3 + 12


def test_an_uncounted_role_makes_the_ceiling_unknown(registered: TestClient) -> None:
    """Zero would claim the blind spot is empty. `None` says nobody counted."""
    observe(
        registered,
        member_counts_complete=False,
        roles=[
            {
                "role_id": str(ADMIN_ROLE),
                "name": "admin",
                "position": 9,
                "member_count": None,
            }
        ],
    )

    snapshot = read(registered)

    assert snapshot["unbannable_members"] is None
    assert snapshot["member_counts_complete"] is False
    assert snapshot["unbannable_roles"][0]["member_count"] is None


def test_a_missing_ban_permission_does_not_hide_the_hierarchy(
    registered: TestClient,
) -> None:
    """Two separate problems with two separate fixes. Folding them together would answer
    "every role" and re-hide the hierarchy the moment the permission was granted."""
    observe(registered, can_ban=False)

    snapshot = read(registered)

    assert snapshot["can_ban"] is False
    assert [role["name"] for role in snapshot["unbannable_roles"]] == ["admin", "moderator"]


def test_a_fresh_snapshot_is_not_stale(registered: TestClient) -> None:
    observe(registered)

    assert read(registered)["stale"] is False


def test_reading_diagnostics_costs_no_discord_call(
    registered: TestClient, discord: FakeDiscord
) -> None:
    """The whole reason the bot reports rather than the backend asking (ADR 0016)."""
    observe(registered)
    discord.calls.clear()

    read(registered)

    assert discord.calls_of("fetch_member") == []


# -- who may read it ---------------------------------------------------------


def test_a_plain_member_may_not_read_diagnostics(registered: TestClient) -> None:
    """It names the guild's roles and how many hold each."""
    observe(registered)

    response = registered.get(f"/guilds/{GUILD}/diagnostics", headers=headers(MEMBER))

    assert response.status_code == 403


def test_administering_one_guild_grants_nothing_over_another(
    registered: TestClient,
) -> None:
    response = registered.put(
        f"/guilds/{OTHER_GUILD}/diagnostics", json=report(), headers=headers("system")
    )
    assert response.status_code in {200, 404}

    refused = registered.get(f"/guilds/{OTHER_GUILD}/diagnostics", headers=headers(GUILD_ADMIN))
    assert refused.status_code == 403


def test_a_pool_manager_is_not_a_guild_administrator(registered: TestClient) -> None:
    """Owning the pools that cause the bans is not owning the guild they land in."""
    observe(registered)

    response = registered.get(f"/guilds/{GUILD}/diagnostics", headers=headers(POOL_MANAGER))

    assert response.status_code == 403


# -- failed bans -------------------------------------------------------------


def _failed_ban(client: TestClient, discord: FakeDiscord, enforcement: Enforcement) -> None:
    """Get one real `failed` outcome on the board, through the real enforcement path."""
    client.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    discord.add_member(GUILD, LISTED_USER, role_ids=frozenset({ADMIN_ROLE, MEMBER_ROLE}))
    discord.fail(
        "ban", guild_id=GUILD, user_id=LISTED_USER, error=ForbiddenError("Missing Permissions")
    )
    client.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "raiding"},
        headers=headers(POOL_MANAGER),
    )
    enforcement.drain()


def test_the_failure_list_reads_only_the_database(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """It renders at once however long it is; explaining one row is a separate ask."""
    _failed_ban(pool, discord, enforcement)
    discord.calls.clear()

    response = pool.get(f"/guilds/{GUILD}/diagnostics/failures", headers=headers(GUILD_ADMIN))

    assert response.status_code == 200, response.text
    (failure,) = response.json()
    assert failure["user_id"] == str(LISTED_USER)
    assert failure["pool_name"] == "spam"
    assert discord.calls == []


def test_an_outranked_target_names_the_roles_to_move(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    _failed_ban(pool, discord, enforcement)
    observe(pool)

    response = pool.get(
        f"/guilds/{GUILD}/diagnostics/failures/{LISTED_USER}", headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 200, response.text
    diagnosis = response.json()
    assert diagnosis["blocker"] == "outranked"
    assert [role["name"] for role in diagnosis["blocking_roles"]] == ["admin"]
    assert diagnosis["timothy_top_role_position"] == TIMOTHY_TOP
    assert diagnosis["detail"] == "Missing Permissions"


def test_a_missing_permission_is_the_whole_answer(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    _failed_ban(pool, discord, enforcement)
    observe(pool, can_ban=False)

    response = pool.get(
        f"/guilds/{GUILD}/diagnostics/failures/{LISTED_USER}", headers=headers(GUILD_ADMIN)
    )

    assert response.json()["blocker"] == "no_ban_permission"
    assert response.json()["blocking_roles"] == []


def test_a_target_who_has_left_is_history(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """Enforcement is reactive (ADR 0004): they are banned at the door if they come back."""
    _failed_ban(pool, discord, enforcement)
    observe(pool)
    del discord.guilds[GUILD].members[LISTED_USER]

    response = pool.get(
        f"/guilds/{GUILD}/diagnostics/failures/{LISTED_USER}", headers=headers(GUILD_ADMIN)
    )

    assert response.json()["blocker"] == "left_guild"


def test_discord_refusing_the_lookup_still_answers(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """Somebody is reading this *because* something is wrong. A 502 here would replace a
    partial explanation with none — and reporting `left_guild` would replace it with a
    wrong one, telling an administrator the problem had solved itself."""
    _failed_ban(pool, discord, enforcement)
    observe(pool)
    discord.fail(
        "fetch_member", guild_id=GUILD, user_id=LISTED_USER, error=ForbiddenError("nope")
    )

    response = pool.get(
        f"/guilds/{GUILD}/diagnostics/failures/{LISTED_USER}", headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 200
    assert response.json()["blocker"] == "unknown"
    assert response.json()["detail"] == "Missing Permissions"


def test_a_lookup_that_failed_still_reports_a_missing_permission(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The verdict that comes from the stored snapshot survives Discord being unreachable,
    and it is the one most worth giving."""
    _failed_ban(pool, discord, enforcement)
    observe(pool, can_ban=False)
    discord.fail(
        "fetch_member", guild_id=GUILD, user_id=LISTED_USER, error=ForbiddenError("nope")
    )

    response = pool.get(
        f"/guilds/{GUILD}/diagnostics/failures/{LISTED_USER}", headers=headers(GUILD_ADMIN)
    )

    assert response.json()["blocker"] == "no_ban_permission"


def test_the_guild_owner_is_out_of_reach(registered: TestClient, discord: FakeDiscord) -> None:
    observe(registered)
    discord.add_member(GUILD, GUILD_OWNER)

    response = registered.get(
        f"/guilds/{GUILD}/diagnostics/failures/{GUILD_OWNER}", headers=headers(GUILD_ADMIN)
    )

    assert response.json()["blocker"] == "guild_owner"


def test_diagnosing_without_a_snapshot_says_so(registered: TestClient) -> None:
    response = registered.get(
        f"/guilds/{GUILD}/diagnostics/failures/{LISTED_USER}", headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 404


# -- asking for a re-check ---------------------------------------------------


def test_a_refresh_request_reaches_the_bot(registered: TestClient) -> None:
    observe(registered)

    asked = registered.post(
        f"/guilds/{GUILD}/diagnostics/refresh", headers=headers(GUILD_ADMIN)
    )
    collected = registered.get("/diagnostics/pending", headers=headers("system"))

    assert asked.status_code == 202
    assert collected.json()["guild_ids"] == [str(GUILD)]


def test_collecting_drains_the_queue(registered: TestClient) -> None:
    """A request answers for one round. Otherwise every poll re-reports every guild."""
    observe(registered)
    registered.post(f"/guilds/{GUILD}/diagnostics/refresh", headers=headers(GUILD_ADMIN))
    registered.get("/diagnostics/pending", headers=headers("system"))

    again = registered.get("/diagnostics/pending", headers=headers("system"))

    assert again.json()["guild_ids"] == []


def test_asking_twice_is_one_re_check(registered: TestClient) -> None:
    observe(registered)
    for _ in range(3):
        registered.post(f"/guilds/{GUILD}/diagnostics/refresh", headers=headers(GUILD_ADMIN))

    collected = registered.get("/diagnostics/pending", headers=headers("system"))

    assert collected.json()["guild_ids"] == [str(GUILD)]


def test_only_an_administrator_of_the_guild_may_ask(registered: TestClient) -> None:
    response = registered.post(f"/guilds/{GUILD}/diagnostics/refresh", headers=headers(MEMBER))

    assert response.status_code == 403


def test_only_the_bot_may_collect(registered: TestClient) -> None:
    response = registered.get("/diagnostics/pending", headers=headers(GUILD_ADMIN))

    assert response.status_code == 403
