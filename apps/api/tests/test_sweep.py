"""The sweep scheduler: a safety net, spread out, that cannot pile up on itself.

ADR 0004 demoted the sweep from the primary enforcement path to a backstop for events
missed while the gateway was down. So what these test is not that it enforces — that is
the worker's job and every other file's subject — but that it queues the right guilds, at
the right times, and never more than once at a time.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from timothy_api.db import Database
from timothy_api.enforcement import Sweeper
from timothy_api.enforcement.pacing import Pacer
from timothy_api.jobs import JobKind
from timothy_api.settings import Settings

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    MANAGEMENT_GUILD,
    OTHER_GUILD,
    Enforcement,
    headers,
    jobs_of,
    wait_until,
)


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"sweep_interval": timedelta(hours=1)}


def sweeps(settings: Settings) -> list[dict[str, Any]]:
    return [job for job in jobs_of(settings) if job["kind"] == JobKind.ENFORCE_GUILD.value]


def stored(when: datetime) -> str:
    """A UTC instant in the naive text form SQLite holds it as."""
    return when.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def test_a_round_queues_one_sweep_per_guild(
    registered: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    assert enforcement.sweep() == 2

    assert [job["payload"]["guild_id"] for job in sweeps(settings)] == [
        MANAGEMENT_GUILD,
        GUILD,
    ]


def test_the_round_is_spread_across_the_interval(
    registered: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    """Queueing every guild at once would make one tick an hour the expensive one, and
    would put the whole fleet's Discord calls in the same minute."""
    base = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    enforcement.sweep(now=lambda: base)

    run_afters = [str(job["run_after"]) for job in sweeps(settings)]
    assert run_afters == [
        stored(base),
        stored(base + timedelta(minutes=30)),
    ]


def test_a_guild_with_a_sweep_still_pending_is_skipped(
    registered: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    """A guild slower than the interval would otherwise accumulate a queue it could never
    work off. Its outstanding job picks up whatever arrived meanwhile — the candidates
    are computed when the job runs, not when it was queued."""
    enforcement.sweep()

    assert enforcement.sweep() == 0
    assert len(sweeps(settings)) == 2


def test_a_finished_sweep_lets_the_next_round_through(
    registered: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    enforcement.sweep()
    enforcement.drain(now=lambda: datetime.now(UTC) + timedelta(hours=2))

    assert enforcement.sweep() == 2


def test_a_paused_guild_is_not_swept(
    registered: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    """It would decide `enforcement_paused` and record nothing, so the job would be a
    Discord-free no-op — but queueing one an hour for a guild under human review is noise
    in the one table an operator is reading."""
    registered.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": True}, headers=headers(GUILD_ADMIN)
    )

    assert enforcement.sweep() == 1
    assert [job["payload"]["guild_id"] for job in sweeps(settings)] == [MANAGEMENT_GUILD]


def test_resuming_puts_a_guild_back_in_the_rotation(
    registered: TestClient, enforcement: Enforcement
) -> None:
    """Resuming queues its own catch-up, so the very next round leaves that guild alone —
    it already has a sweep pending — and the round after picks it up normally."""
    registered.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": True}, headers=headers(GUILD_ADMIN)
    )
    registered.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": False}, headers=headers(GUILD_ADMIN)
    )

    assert enforcement.sweep() == 1
    enforcement.drain(now=lambda: datetime.now(UTC) + timedelta(hours=2))
    assert enforcement.sweep() == 2


def test_a_new_guild_joins_the_next_round(
    registered: TestClient, enforcement: Enforcement
) -> None:
    enforcement.sweep()
    enforcement.drain(now=lambda: datetime.now(UTC) + timedelta(hours=2))
    registered.put(f"/guilds/{OTHER_GUILD}", headers=headers("system"))

    assert enforcement.sweep() == 3


def test_sweeping_with_no_guilds_does_nothing(
    client: TestClient, enforcement: Enforcement
) -> None:
    assert enforcement.sweep() == 0


@pytest.mark.anyio
async def test_run_forever_queues_a_round_immediately_then_waits(
    registered: TestClient, settings: Settings
) -> None:
    """A restart is one of the gaps the sweep exists to cover, so the first round does not
    wait an hour to find out what was missed while the process was down."""
    pacer = Pacer()
    database = Database(settings.database_url)
    sweeper = Sweeper(database.sessions, settings, pacer=pacer)

    task = asyncio.create_task(sweeper.run_forever())
    await wait_until(lambda: bool(sweeps(settings)))
    pacer.stop()
    try:
        await asyncio.wait_for(task, timeout=2)
    finally:
        await database.dispose()

    assert len(sweeps(settings)) == 2
    assert not task.cancelled()
