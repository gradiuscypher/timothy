"""Reading back what Timothy did in a guild.

Phase 6 wants this as per-guild enforcement history. It is here now because it is the
only way to see from outside whether a fan-out landed, which pool a ban is attributed to,
and what a `failed` row is still waiting on.
"""

from fastapi.testclient import TestClient

from timothy_core.enums import OutcomeStatus
from timothy_core.ports.discord import ForbiddenError
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    MEMBER,
    POOL_ADMIN,
    Enforcement,
    headers,
)

OTHER_USER = 300_000_000_000_000_007


def enforced(client: TestClient, discord: FakeDiscord, enforcement: Enforcement) -> None:
    client.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    for user_id in (LISTED_USER, OTHER_USER):
        discord.add_member(GUILD, user_id)
        client.post(
            "/pools/spam/listings",
            json={"user_id": str(user_id), "reason": "raiding"},
            headers=headers(POOL_ADMIN),
        )
    discord.fail("ban", guild_id=GUILD, user_id=OTHER_USER, error=ForbiddenError("no rights"))
    enforcement.drain()


def test_a_guild_administrator_reads_its_own_history(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    enforced(pool, discord, enforcement)

    rows = pool.get(f"/guilds/{GUILD}/enforcement", headers=headers(GUILD_ADMIN)).json()

    assert {row["user_id"] for row in rows} == {str(LISTED_USER), str(OTHER_USER)}
    assert {row["status"] for row in rows} == {
        OutcomeStatus.BANNED.value,
        OutcomeStatus.FAILED.value,
    }


def test_the_failed_rows_can_be_asked_for_on_their_own(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """The interesting filter: what is still outstanding in this guild, and why."""
    enforced(pool, discord, enforcement)

    rows = pool.get(
        f"/guilds/{GUILD}/enforcement?status=failed", headers=headers(GUILD_ADMIN)
    ).json()

    assert [row["user_id"] for row in rows] == [str(OTHER_USER)]
    assert "no rights" in rows[0]["reason"]


def test_snowflakes_come_back_as_strings(
    pool: TestClient, discord: FakeDiscord, enforcement: Enforcement
) -> None:
    """Past 2^53 a JavaScript number stops being exact, so these never cross as numbers."""
    enforced(pool, discord, enforcement)

    rows = pool.get(f"/guilds/{GUILD}/enforcement", headers=headers(GUILD_ADMIN)).json()

    assert all(isinstance(row["guild_id"], str) for row in rows)
    assert all(isinstance(row["user_id"], str) for row in rows)


def test_only_that_guilds_administrators_may_read_it(registered: TestClient) -> None:
    """It names the users this guild has banned and why, which the management guild's
    pool owners do not need in order to own pools."""
    assert (
        registered.get(f"/guilds/{GUILD}/enforcement", headers=headers(POOL_ADMIN)).status_code
        == 403
    )
    assert (
        registered.get(f"/guilds/{GUILD}/enforcement", headers=headers(MEMBER)).status_code
        == 403
    )
    assert (
        registered.get(f"/guilds/{GUILD}/enforcement", headers=headers(GUILD_ADMIN)).status_code
        == 200
    )


def test_a_guild_timothy_is_not_in_is_a_404(client: TestClient) -> None:
    assert (
        client.get(f"/guilds/{GUILD}/enforcement", headers=headers(GUILD_ADMIN)).status_code
        == 404
    )
