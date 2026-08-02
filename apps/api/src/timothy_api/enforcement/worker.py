"""Claim a job, run it, and decide what to do when it goes wrong.

One worker, in the API's own process and on its event loop (ADR 0003). SQLite has a
single writer, and a second worker would spend its life contending for it; the throughput
that matters is Discord's rate limits, not the queue's.

**What counts as a job failure is narrow.** A guild that refuses a ban, a user who
outranks Timothy, a channel that has been deleted — none of those fail the job. They are
recorded as `failed` enforcement outcomes by the enforcer, and the sweep retries them
when the world may have changed. Running the same job again in eight seconds would just
collect the same refusal. What reaches the retry logic here is the job failing to *run*:
a malformed payload, an unhandled kind, the database going away underneath it.

**Retries are the plainest possible exponential backoff**, capped, and give up after
`job_max_attempts` with the reason written to `jobs.last_error`. A job that has given up
is a row an operator can read, which is what that column is for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import select, update

from timothy_api.enforcement.handlers import HANDLERS
from timothy_api.enforcement.pacing import Pacer
from timothy_api.jobs import JobKind
from timothy_core.db.models import Job
from timothy_core.enums import JobStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from timothy_api.enforcement.engine import Enforcer
    from timothy_api.settings import Settings

log = logging.getLogger(__name__)

BACKOFF_BASE: Final = 4.0
BACKOFF_CAP: Final = timedelta(minutes=10)
ERROR_LIMIT: Final = 1000
"""`last_error` is for a human reading a row, not for a stack trace. A repr long enough
to need scrolling past is one nobody reads."""


@dataclass(frozen=True, slots=True)
class JobContext:
    """What a handler is given: sessions of its own, and the enforcer to act through."""

    sessions: async_sessionmaker[AsyncSession]
    enforcer: Enforcer
    settings: Settings


class Worker:
    """Drains the job queue."""

    def __init__(
        self,
        context: JobContext,
        *,
        pacer: Pacer | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Work through `context`, pausing on `pacer` when the queue is empty."""
        self.context = context
        self.pacer = pacer if pacer is not None else Pacer()
        self._now = now

    def stop(self) -> None:
        """Ask :meth:`run_forever` to finish after the job it is on."""
        self.pacer.stop()

    async def recover(self) -> int:
        """Return jobs left `running` by a crash to the queue.

        Safe because every handler is idempotent in the way that matters: banning an
        already-banned user refreshes the reason, an outcome row is written in place, and
        a revert whose unban already landed finds `NotFoundError` and clears the
        attribution anyway.
        """
        async with self.context.sessions() as session:
            stale = list(
                await session.scalars(select(Job.id).where(Job.status == JobStatus.RUNNING))
            )
            if stale:
                await session.execute(
                    update(Job).where(Job.id.in_(stale)).values(status=JobStatus.PENDING)
                )
                await session.commit()
        if stale:
            log.warning("returned %d interrupted job(s) to the queue", len(stale))
        return len(stale)

    async def run_once(self) -> bool:
        """Run at most one job. `False` when there was nothing due.

        The unit the tests drive, so that what ran and when is not a matter of timing.
        """
        claimed = await self._claim()
        if claimed is None:
            return False

        job_id, kind, payload = claimed
        try:
            handler = HANDLERS[JobKind(kind)]
            await handler(self.context, payload)
        except Exception as error:
            log.exception("job %d (%s) failed", job_id, kind)
            await self._reschedule(job_id, error)
        else:
            await self._finish(job_id)
        return True

    async def drain(self) -> int:
        """Run jobs until nothing is due, and say how many. Bounded by what is queued."""
        done = 0
        while await self.run_once():
            done += 1
        return done

    async def run_forever(self) -> None:
        """Poll the queue until asked to stop. The lifespan's task.

        A job that raises has already been rescheduled by :meth:`run_once`; anything that
        reaches here is the queue machinery itself failing, and the loop outlives it
        rather than leaving the process running with nothing draining.
        """
        await self.recover()
        interval = self.context.settings.job_poll_interval.total_seconds()
        while not self.pacer.stopping:
            try:
                if await self.run_once():
                    continue
            except Exception:
                log.exception("worker poll failed")
            if await self.pacer.pause(interval):
                return

    # -- the queue -----------------------------------------------------------

    async def _claim(self) -> tuple[int, str, dict[str, int]] | None:
        """Take the oldest job that is due, and mark it running.

        Read out as plain values rather than handed on as an ORM object: the handler runs
        in sessions of its own, and a `Job` attached to this one would be a detached
        instance the moment this transaction closes.
        """
        async with self.context.sessions() as session:
            job = await session.scalar(
                select(Job)
                .where(Job.status == JobStatus.PENDING, Job.run_after <= self._now())
                .order_by(Job.id)
                .limit(1)
            )
            if job is None:
                return None
            job.status = JobStatus.RUNNING
            job.attempts += 1
            claimed = (job.id, job.kind, dict(job.payload))
            await session.commit()
        return claimed

    async def _finish(self, job_id: int) -> None:
        async with self.context.sessions() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status=JobStatus.DONE, last_error=None)
            )
            await session.commit()

    async def _reschedule(self, job_id: int, error: Exception) -> None:
        """Back off and try again, or give up and say why."""
        message = f"{type(error).__name__}: {error}"[:ERROR_LIMIT]
        async with self.context.sessions() as session:
            job = await session.get(Job, job_id)
            if job is None:  # pragma: no cover — nothing deletes jobs
                return
            job.last_error = message
            if job.attempts >= self.context.settings.job_max_attempts:
                job.status = JobStatus.FAILED
                log.error(
                    "job %d (%s) abandoned after %d attempts", job_id, job.kind, job.attempts
                )
            else:
                job.status = JobStatus.PENDING
                job.run_after = self._now() + _backoff(job.attempts)
            await session.commit()


def _backoff(attempts: int) -> timedelta:
    """Four seconds, then sixteen, then a minute or so, capped at ten minutes."""
    seconds = BACKOFF_BASE**attempts
    return min(timedelta(seconds=seconds), BACKOFF_CAP)
