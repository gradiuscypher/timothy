"""What the caller is told when the failure is not theirs.

Discord being unreachable must never read as "you are not allowed" — a moderator told
that goes and checks their roles instead of trying again.
"""

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from timothy_api.app import create_app
from timothy_api.settings import Settings
from timothy_core.ports.discord import (
    DiscordUnavailableError,
    ForbiddenError,
    RateLimitedError,
)
from timothy_core.ports.fake import FakeDiscord

from .conftest import MANAGEMENT_GUILD, POOL_ADMIN, headers


def failing(discord: FakeDiscord, error: Exception) -> None:
    discord.fail(
        "guild_permissions",
        guild_id=MANAGEMENT_GUILD,
        user_id=POOL_ADMIN,
        error=error,  # ty: ignore[invalid-argument-type]
    )


def test_discord_being_down_is_a_503(client: TestClient, discord: FakeDiscord) -> None:
    failing(discord, DiscordUnavailableError("down"))

    response = client.post("/pools", json={"name": "spam"}, headers=headers(POOL_ADMIN))

    assert response.status_code == 503


def test_a_rate_limit_is_a_429_that_says_how_long(
    client: TestClient, discord: FakeDiscord
) -> None:
    failing(discord, RateLimitedError(retry_after=2.5))

    response = client.post("/pools", json={"name": "spam"}, headers=headers(POOL_ADMIN))

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3"


def test_any_other_discord_refusal_is_a_502(client: TestClient, discord: FakeDiscord) -> None:
    """Not a 403: the caller may be perfectly entitled, and Timothy is the one that
    cannot act."""
    failing(discord, ForbiddenError("timothy cannot see that guild"))

    response = client.post("/pools", json={"name": "spam"}, headers=headers(POOL_ADMIN))

    assert response.status_code == 502


def test_a_constraint_violation_that_slips_through_is_a_409(
    settings: Settings, discord: FakeDiscord
) -> None:
    """Handlers check for the obvious collisions and give a better message. This catches
    the ones that could land between a check and a commit."""
    app = create_app(settings, discord_port=discord)

    @app.get("/boom")
    async def boom() -> None:
        statement = "INSERT INTO pools (name) VALUES ('spam')"
        cause = Exception("UNIQUE constraint failed")
        raise IntegrityError(statement, None, cause)

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom").status_code == 409
