"""The queue machinery itself: claiming, retrying, giving up, and recovering.

Deliberately separate from the enforcement tests. Those are about what Timothy decides;
these are about the fact that a job runs exactly once when it works, and that a job which
cannot run says so in a row a human can read rather than only in a log line.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from timothy_api.db import Database
from timothy_api.enforcement import Enforcer, JobContext, SelfUnbans, Worker
from timothy_api.enforcement.handlers import HANDLERS
from timothy_api.enforcement.pacing import Pacer
from timothy_api.jobs import JobKind
from timothy_api.settings import Settings
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    POOL_ADMIN,
    Enforcement,
    headers,
    insert_job,
    jobs_of,
    wait_until,
)


def at(when: datetime) -> Callable[[], datetime]:
    """A clock stopped at one moment.

    Retries have to be driven a step at a time, from a clock that stands still within
    each step: the worker dates a failed job forward from whatever clock it was given, so
    a clock that advanced *during* an attempt would find its own reschedule already due
    and burn every attempt in one go.
    """
    return lambda: when


def test_every_job_kind_has_a_handler() -> None:
    """An unhandled kind would be a job that fails its way to `failed` with a `KeyError`
    for a reason, which is a deployment bug found in production."""
    assert set(HANDLERS) == set(JobKind)


def test_a_job_that_runs_is_marked_done_once(
    pool: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )

    assert enforcement.drain() == 1
    assert enforcement.drain() == 0
    assert [job["status"] for job in jobs_of(settings)] == ["done"]


def test_an_unrecognised_kind_is_retried_then_abandoned(
    pool: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    """It retries — and then it stops, rather than retrying a broken job forever."""
    insert_job(settings, "no_such_kind", {})

    base = datetime.now(UTC)
    for attempt in range(1, settings.job_max_attempts + 1):
        assert enforcement.run_once(now=at(base + timedelta(hours=attempt)))
        assert jobs_of(settings)[0]["attempts"] == attempt

    assert not enforcement.run_once(now=at(base + timedelta(days=1)))
    job = jobs_of(settings)[0]
    assert job["status"] == "failed"
    assert "no_such_kind" in str(job["last_error"])


def test_a_broken_payload_is_a_job_failure_not_a_silent_success(
    enforcement: Enforcement, settings: Settings
) -> None:
    """A handler that cannot find the key it needs has not enforced anything, and must
    not be recorded as though it had."""
    insert_job(settings, JobKind.ENFORCE_LISTING.value, {})

    enforcement.drain()

    job = jobs_of(settings)[0]
    assert job["status"] == "pending"
    assert "KeyError" in str(job["last_error"])


def test_a_failing_job_backs_off_rather_than_spinning(
    enforcement: Enforcement, settings: Settings
) -> None:
    """Without this a broken job would burn every attempt inside one `drain`."""
    insert_job(settings, "no_such_kind", {})

    enforcement.drain()

    job = jobs_of(settings)[0]
    assert job["attempts"] == 1
    assert str(job["run_after"]) > datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def test_a_successful_retry_clears_the_error(
    pool: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    """`last_error` describes the state a job is in, not everything that ever happened to
    it — the audit log is where history lives."""
    insert_job(settings, JobKind.ENFORCE_GUILD_USER.value, {"guild_id": GUILD})
    enforcement.drain()
    assert jobs_of(settings)[0]["last_error"] is not None

    # The same job, given the key it was missing.
    insert_job(
        settings, JobKind.ENFORCE_GUILD_USER.value, {"guild_id": GUILD, "user_id": LISTED_USER}
    )
    enforcement.drain()

    assert jobs_of(settings)[-1]["last_error"] is None
    assert jobs_of(settings)[-1]["status"] == "done"


def test_a_job_interrupted_by_a_crash_is_returned_to_the_queue(
    pool: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    """A process killed mid-job leaves the row `running`, and nothing would ever claim it
    again."""
    pool.post(
        "/pools/spam/listings",
        json={"user_id": str(LISTED_USER), "reason": "spam"},
        headers=headers(POOL_ADMIN),
    )
    engine_rows = jobs_of(settings)
    assert engine_rows[0]["status"] == "pending"

    _mark_running(settings)
    assert enforcement.recover() == 1

    assert jobs_of(settings)[0]["status"] == "pending"
    assert enforcement.drain() == 1


def test_a_job_dated_forward_is_not_claimed_yet(
    pool: TestClient, enforcement: Enforcement, settings: Settings
) -> None:
    """What backoff and the sweep's staggering both rely on."""
    enforcement.sweep()

    assert enforcement.drain() == 1  # only the guild whose turn is now
    assert enforcement.drain(now=lambda: datetime.now(UTC) + timedelta(hours=2)) == 1


@pytest.mark.anyio
async def test_the_loop_stops_when_asked_rather_than_being_cancelled() -> None:
    """Cancelling a task part-way through a transaction leaks its connection, so the
    loops are asked to finish instead. See `timothy_api.enforcement.pacing`."""
    pacer = Pacer()
    rounds = 0

    async def loop() -> None:
        nonlocal rounds
        while not pacer.stopping:
            rounds += 1
            if await pacer.pause(3600):
                return

    task = asyncio.create_task(loop())
    await asyncio.sleep(0)
    pacer.stop()
    await asyncio.wait_for(task, timeout=1)

    assert rounds == 1
    assert task.done()
    assert not task.cancelled()


@pytest.mark.anyio
async def test_run_forever_drains_and_then_waits(
    pool: TestClient, settings: Settings, discord: FakeDiscord
) -> None:
    """The lifespan's task, driven to a halt on purpose: it recovers, drains what is due,
    and stops at the first pause once asked to."""
    pool.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": "ban"},
        headers=headers(GUILD_ADMIN),
    )
    pacer = Pacer()
    database = Database(settings.database_url)
    worker = Worker(
        JobContext(
            sessions=database.sessions,
            enforcer=Enforcer(
                discord=discord, settings=settings, self_unbans=SelfUnbans(), sleep=_no_wait
            ),
            settings=settings,
        ),
        pacer=pacer,
    )

    task = asyncio.create_task(worker.run_forever())
    await wait_until(lambda: jobs_of(settings)[0]["status"] == "done")
    pacer.stop()
    try:
        await asyncio.wait_for(task, timeout=2)
    finally:
        await database.dispose()

    assert task.done()
    assert not task.cancelled()


async def _no_wait(_seconds: float) -> None:
    return


def _mark_running(settings: Settings) -> None:
    from sqlalchemy import create_engine, text  # noqa: PLC0415 — one caller, one place

    from timothy_core.migrations import sync_url  # noqa: PLC0415

    engine = create_engine(sync_url(settings.database_url))
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE jobs SET status = 'running'"))
    finally:
        engine.dispose()
