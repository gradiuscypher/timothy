"""Fixtures for the API.

Every test drives the real application — real routing, real FastAPI dependencies, real
SQLite built by the real migrations — against an in-memory Discord. Nothing is mocked
except the far side of the port, which is the arrangement ADR 0007 exists to make
possible.
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from timothy_api.app import create_app
from timothy_api.db import Database
from timothy_api.enforcement import Enforcer, JobContext, SelfUnbans, Sweeper, Worker
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
def settings_overrides() -> dict[str, Any]:
    """What a module wants different. Override this fixture, not `settings`."""
    return {}


@pytest.fixture
def settings(tmp_path: Path, settings_overrides: dict[str, Any]) -> Settings:
    """Every field set explicitly, so a developer's own `TIMOTHY_*` cannot leak in.

    Two departures from production defaults, both so that tests say what they mean:

    * **Workers off.** Most of this suite is about what the API *records*, including what
      it enqueues, and a background worker draining the queue mid-request would make
      every such assertion a race. The tests that are about the worker drive it a job at
      a time through the `enforcement` fixture.
    * **Dry run off.** Production fails safe to on (ADR 0007), which is right, and
      `test_settings.py` is where that is asserted. A suite that inherited it would be a
      suite in which Timothy never bans anybody, so the enforcement tests would all pass
      vacuously. `test_dry_run.py` turns it back on.
    """
    fields: dict[str, Any] = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'timothy.db'}",
        "internal_token": TOKEN,
        "management_guild_id": MANAGEMENT_GUILD,
        "auto_subscribe_pool": "global",
        "workers_enabled": False,
        "dry_run": False,
        **settings_overrides,
    }
    return Settings(**fields)


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


WAIT_LIMIT = 2.0


async def wait_until(ready: Callable[[], bool]) -> None:
    """Poll until `ready()`, or give up after `WAIT_LIMIT`.

    For the two tests that let a background loop run for real. What they are waiting on
    is a row in the database, which the loop does not signal — so this polls, rather than
    waiting on an event that does not exist — and bounds itself, so a loop that never
    gets there fails the test instead of hanging the suite.
    """
    async with asyncio.timeout(WAIT_LIMIT):
        while not ready():  # noqa: ASYNC110 — the signal is a row, not an event
            await asyncio.sleep(0.01)


async def _instant(_seconds: float) -> None:
    """Backoff without the waiting. What is under test is that Timothy retries, not that
    it can count to four."""
    return


class Enforcement:
    """The worker half of the process, driven a step at a time from a sync test.

    In production the worker shares the API's event loop and engine (ADR 0003). Here the
    API is inside `TestClient`'s thread, so this opens its own engine on the same SQLite
    file for the duration of each call. What it deliberately does *not* duplicate is the
    Discord fake or the self-unban registry: those are the same objects the application
    holds, because a revert issued by the worker has to be recognised by an event
    arriving at the API, and a test where those were two registries would prove nothing.
    """

    def __init__(
        self, settings: Settings, discord: FakeDiscord, self_unbans: SelfUnbans
    ) -> None:
        self.settings = settings
        self.discord = discord
        self.self_unbans = self_unbans

    def _run[T](self, work: Callable[[JobContext], Awaitable[T]]) -> T:
        async def main() -> T:
            database = Database(self.settings.database_url)
            try:
                return await work(
                    JobContext(
                        sessions=database.sessions,
                        enforcer=Enforcer(
                            discord=self.discord,
                            settings=self.settings,
                            self_unbans=self.self_unbans,
                            sleep=_instant,
                        ),
                        settings=self.settings,
                    )
                )
            finally:
                await database.dispose()

        return asyncio.run(main())

    def drain(self, *, now: Callable[[], datetime] | None = None) -> int:
        """Run every job that is due, and say how many ran."""
        return self._run(lambda context: _worker(context, now).drain())

    def run_once(self, *, now: Callable[[], datetime] | None = None) -> bool:
        """Run at most one job."""
        return self._run(lambda context: _worker(context, now).run_once())

    def recover(self) -> int:
        """Return jobs left `running` by a crash to the queue."""
        return self._run(lambda context: _worker(context, None).recover())

    def sweep(self, *, now: Callable[[], datetime] | None = None) -> int:
        """Queue one round of guild sweeps, and say how many were queued."""

        def round_(context: JobContext) -> Awaitable[int]:
            sweeper = (
                Sweeper(context.sessions, self.settings, now=now)
                if now is not None
                else Sweeper(context.sessions, self.settings)
            )
            return sweeper.schedule_round()

        return self._run(round_)


def _worker(context: JobContext, now: Callable[[], datetime] | None) -> Worker:
    return Worker(context, now=now) if now is not None else Worker(context)


@pytest.fixture
def enforcement(client: TestClient, settings: Settings, discord: FakeDiscord) -> Enforcement:
    """The worker, sharing the running application's Discord and self-unban registry."""
    return Enforcement(settings, discord, client.app.state.self_unbans)  # ty: ignore[unresolved-attribute]


def jobs_of(settings: Settings) -> list[dict[str, Any]]:
    """Every job row, whatever its status — what `enqueued` reads, plus the bookkeeping."""
    engine = create_engine(sync_url(settings.database_url))
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, kind, payload, status, attempts, last_error, run_after "
                    "FROM jobs ORDER BY id"
                )
            ).mappings()
            return [dict(row) | {"payload": json.loads(row["payload"])} for row in rows]
    finally:
        engine.dispose()


def insert_job(settings: Settings, kind: str, payload: dict[str, int]) -> None:
    """Put a job on the queue directly.

    For the shapes no route can produce: an unrecognised kind, a payload missing the key
    its handler needs. Those are the ones the worker's retry logic is actually for.
    """
    engine = create_engine(sync_url(settings.database_url))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO jobs (kind, payload, run_after, attempts, status, created_at)"
                    " VALUES (:kind, :payload, :now, 0, 'pending', :now)"
                ),
                {
                    "kind": kind,
                    "payload": json.dumps(payload),
                    # The text form SQLAlchemy's DateTime writes. Handing sqlite3 a
                    # `datetime` uses its deprecated default adapter instead.
                    "now": datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
                },
            )
    finally:
        engine.dispose()


def outcomes_of(settings: Settings) -> list[dict[str, Any]]:
    """Every enforcement outcome, as plain rows."""
    engine = create_engine(sync_url(settings.database_url))
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT guild_id, user_id, pool_id, status, reason FROM "
                    "enforcement_outcomes ORDER BY guild_id, user_id, pool_id"
                )
            ).mappings()
            return [dict(row) for row in rows]
    finally:
        engine.dispose()


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
