"""Fixtures for the API.

Every test drives the real application — real routing, real FastAPI dependencies, real
SQLite built by the real migrations — against an in-memory Discord. Nothing is mocked
except the far side of the port, which is the arrangement ADR 0007 exists to make
possible.
"""

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from timothy_api.app import create_app
from timothy_api.settings import Settings
from timothy_core.migrations import sync_url
from timothy_core.ports.discord import GuildPermissions
from timothy_core.ports.fake import FakeDiscord

TOKEN = "internal-token-for-tests"

MANAGEMENT_GUILD = 100_000_000_000_000_001
GUILD = 100_000_000_000_000_002
OTHER_GUILD = 100_000_000_000_000_003

POOL_ADMIN = 200_000_000_000_000_001
"""Administrator in the management guild: owns pools and listings."""

GUILD_ADMIN = 200_000_000_000_000_002
"""Administrator in GUILD, and an ordinary member of the management guild."""

MEMBER = 200_000_000_000_000_003
"""In a guild Timothy is in, with no permissions anywhere."""

OUTSIDER = 200_000_000_000_000_004
"""In no guild Timothy is in."""

LISTED_USER = 300_000_000_000_000_001
CHANNEL = 400_000_000_000_000_001


def headers(
    actor: int | str | None = POOL_ADMIN, *, token: str | None = TOKEN
) -> dict[str, str]:
    """Credentials for one call: the service token, and who it is on behalf of."""
    sent = {}
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    if actor is not None:
        sent["X-Timothy-Actor"] = actor if isinstance(actor, str) else f"user:{actor}"
    return sent


@pytest.fixture(autouse=True)
def _no_ambient_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the developer's own `TIMOTHY_*` from every test.

    `Settings` reads the environment, so a shell that has a real management guild or a
    real `DRY_RUN` in it would otherwise change what the tests are testing.
    """
    for name in list(os.environ):
        if name.startswith("TIMOTHY_"):
            monkeypatch.delenv(name)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def discord() -> FakeDiscord:
    """A Discord where the management guild and one subscribing guild exist."""
    fake = FakeDiscord()
    fake.add_guild(MANAGEMENT_GUILD)
    fake.add_guild(GUILD)
    fake.add_guild(OTHER_GUILD)
    fake.add_channel(CHANNEL, GUILD)

    fake.add_member(
        MANAGEMENT_GUILD, POOL_ADMIN, permissions=GuildPermissions.administrator_only()
    )
    fake.add_member(MANAGEMENT_GUILD, GUILD_ADMIN)
    fake.add_member(GUILD, GUILD_ADMIN, permissions=GuildPermissions.administrator_only())
    fake.add_member(GUILD, MEMBER)
    return fake


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Every field set explicitly, so a developer's own `TIMOTHY_*` cannot leak in."""
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'timothy.db'}",
        internal_token=TOKEN,
        management_guild_id=MANAGEMENT_GUILD,
        auto_subscribe_pool="global",
    )


@pytest.fixture
def client(settings: Settings, discord: FakeDiscord) -> Iterator[TestClient]:
    """The application, migrated and wired, for the duration of one test."""
    with TestClient(create_app(settings, discord_port=discord)) as test_client:
        yield test_client


@pytest.fixture
def enqueued(settings: Settings) -> Callable[[], list[tuple[str, dict[str, int]]]]:
    """Read the job queue directly.

    Phase 2 has no worker and no route that exposes `jobs`, but what a mutation enqueues
    is part of what the mutation means, so the tests look at the table. Engines are
    disposed rather than dropped: a pooled SQLite connection collected by the GC raises
    during finalisation, and `filterwarnings = ["error"]` turns that into a failure in
    whichever test runs next.
    """

    def read() -> list[tuple[str, dict[str, int]]]:
        engine = create_engine(sync_url(settings.database_url))
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text("SELECT kind, payload FROM jobs ORDER BY id")
                ).all()
        finally:
            engine.dispose()
        return [(kind, json.loads(payload)) for kind, payload in rows]

    return read


@pytest.fixture
def registered(client: TestClient) -> TestClient:
    """The same client, with both guilds registered as Timothy having joined them."""
    for guild_id in (MANAGEMENT_GUILD, GUILD):
        response = client.put(f"/guilds/{guild_id}", headers=headers("system"))
        assert response.status_code == 200
    return client


@pytest.fixture
def pool(registered: TestClient) -> TestClient:
    """A registered stack with one pool, `spam`, owned by the management guild."""
    response = registered.post(
        "/pools",
        json={"name": "spam", "description": "spammers"},
        headers=headers(POOL_ADMIN),
    )
    assert response.status_code == 201
    return registered
