"""Claim a job, run it, and decide what to do when it goes wrong.

Workers run in the API's own process and on its event loop (ADR 0003).

**A worker claims only the kinds it was given, and the sets are disjoint.** For a long
time there was exactly one worker, on the argument that SQLite has a single writer and a
second worker would only contend for it. That argument was about the wrong bottleneck.
The queue is not FIFO-fair across kinds that cost wildly different amounts: an
`enforce_guild` sweep is a `fetch_member` per candidate issued serially and legitimately
runs for hours, and while it held the only worker every `enforce_guild_user` behind it —
the job that bans someone who just *joined* — waited that long too. A user walking into a
guild is the latency-critical path and a weekly safety net is not, so they no longer share
a queue position. `app` runs one worker for the sweeps and one for everything else.

The partition is also what keeps claiming safe without a lock. `_claim` is a `SELECT`
then an `UPDATE` in one transaction, which two workers looking at the same rows could
interleave; disjoint kind sets mean they never see the same row at all. The same goes for
:meth:`Worker.recover`, which would otherwise return the *other* worker's in-flight job
to the queue at startup and have both run it.

SQLite still has one writer, and that is still fine: every handler commits per
(guild, user) pair rather than across a fan-out, and the engine runs in WAL with a five
second `busy_timeout`, so the two workers interleave short writes rather than blocking on
one long one.

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
    from sqlalchemy.sql.elements import ColumnElement

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


SWEEP_KINDS: Final = frozenset({JobKind.ENFORCE_GUILD})
"""The slow half: a whole-guild sweep, which runs for as long as the guild is large.

One kind, and the split is drawn here rather than by a `slow` flag on the row because
this is a property of the *work*, not of a particular job — every `enforce_guild` is a
fan-out over every unsettled candidate in the guild, and none of the others are.
"""


@dataclass(frozen=True, slots=True)
class Claim:
    """Which rows a worker will take off the queue.

    Two ways to say it, and the asymmetry is deliberate rather than an accident of
    modelling. The sweep worker names the kinds it wants; the other one names the kinds it
    does *not*, so that everything else is its problem — including a `kind` this build has
    never heard of, which is what a row written by a newer deploy or by hand looks like.
    Stated as two allow-lists instead, such a row would match neither worker and sit
    `pending` forever: no handler, no error, no `last_error`, nothing to read. Owned by
    one worker it fails on `HANDLERS[...]` in the ordinary way and lands in `failed` with
    a reason, which is what the test suite pins.
    """

    kinds: frozenset[JobKind]
    """The kinds this claim is about."""

    inverted: bool = False
    """Whether `kinds` is what to take (`False`) or what to leave (`True`)."""

    def clause(self) -> ColumnElement[bool]:
        """The `WHERE` fragment that selects this claim's rows."""
        values = [kind.value for kind in self.kinds]
        return Job.kind.not_in(values) if self.inverted else Job.kind.in_(values)


EVERYTHING: Final = Claim(kinds=frozenset(), inverted=True)
"""One worker, the whole queue. The default, and what the tests drive."""

SWEEPS: Final = Claim(kinds=SWEEP_KINDS)
"""The guild sweeps, and only those."""

REACTIVE: Final = Claim(kinds=SWEEP_KINDS, inverted=True)
"""Everything a sweep is not: the enforcement a mutation or a join implies, the reverts,
the name backfill, and any kind this build does not recognise. Bounded work that somebody
is usually waiting on."""


class Worker:
    """Drains the job queue."""

    def __init__(
        self,
        context: JobContext,
        *,
        claim: Claim = EVERYTHING,
        name: str = "worker",
        pacer: Pacer | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Work through `context`, pausing on `pacer` when the queue is empty.

        Args:
            context: sessions, enforcer and settings the handlers run against.
            claim: which rows this worker takes. The whole queue by default, which is
                what the tests want — they drive one worker through whatever they
                enqueued. `app` passes the two disjoint halves.
            name: what this worker calls itself in the log, so two of them draining the
                same queue can be told apart.
            pacer: the wait between empty polls, and the way to ask the loop to stop.
            now: the clock, injected so retry scheduling is not a matter of timing.
        """
        self.context = context
        self.claim = claim
        self.name = name
        self.pacer = pacer if pacer is not None else Pacer()
        self._now = now

    def stop(self) -> None:
        """Ask :meth:`run_forever` to finish after the job it is on."""
        self.pacer.stop()

    async def recover(self) -> int:
        """Return this worker's jobs, left `running` by a crash, to the queue.

        Safe because every handler is idempotent in the way that matters: banning an
        already-banned user refreshes the reason, an outcome row is written in place, and
        a revert whose unban already landed finds `NotFoundError` and clears the
        attribution anyway.

        Scoped to `claim` for a reason that is not tidiness. Both workers start together
        and each recovers before its first claim, so an unscoped sweep would have the
        reactive worker reset the sweep worker's freshly-claimed job to `pending` — and
        then both would run it.
        """
        async with self.context.sessions() as session:
            stale = list(
                await session.scalars(
                    select(Job.id).where(
                        Job.status == JobStatus.RUNNING, self.claim.clause()
                    )
                )
            )
            if stale:
                await session.execute(
                    update(Job).where(Job.id.in_(stale)).values(status=JobStatus.PENDING)
                )
                await session.commit()
        if stale:
            log.warning(
                "%s returned %d interrupted job(s) to the queue",
                self.name,
                len(stale),
                extra={"worker": self.name},
            )
        return len(stale)

    async def run_once(self) -> bool:
        """Run at most one job. `False` when there was nothing due.

        The unit the tests drive, so that what ran and when is not a matter of timing.

        Both ends of a job are logged, and the pair is the point. One job runs at a time
        *per worker*, so "started" with no "finished" after it is the answer to "what is
        this worker waiting on" — the question the timestamps alone cannot settle, because
        a sweep of a large guild legitimately takes half an hour and looks identical to a
        wedge until it ends. `worker` is in the `extra` for exactly that reason: with two
        of them draining one table, an unpaired "started" only means something once you
        can tell whose it is. The `extra` fields are what make all of this a filter in the
        log store rather than a regex over sentences (ADR 0015).
        """
        claimed = await self._claim()
        if claimed is None:
            return False

        job_id, kind, payload = claimed
        started = self._now()
        log.info(
            "%s: job %d (%s) started",
            self.name,
            job_id,
            kind,
            extra={
                "worker": self.name,
                "job_id": job_id,
                "job_kind": kind,
                "job_payload": payload,
            },
        )
        try:
            handler = HANDLERS[JobKind(kind)]
            await handler(self.context, payload)
        except Exception as error:
            log.exception(
                "%s: job %d (%s) failed",
                self.name,
                job_id,
                kind,
                extra={"worker": self.name, "job_id": job_id, "job_kind": kind},
            )
            await self._reschedule(job_id, error)
        else:
            await self._finish(job_id)
            log.info(
                "%s: job %d (%s) finished in %.1fs",
                self.name,
                job_id,
                kind,
                (self._now() - started).total_seconds(),
                extra={
                    "worker": self.name,
                    "job_id": job_id,
                    "job_kind": kind,
                    "seconds": (self._now() - started).total_seconds(),
                },
            )
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
                log.exception("%s poll failed", self.name, extra={"worker": self.name})
            if await self.pacer.pause(interval):
                return

    # -- the queue -----------------------------------------------------------

    async def _claim(self) -> tuple[int, str, dict[str, int]] | None:
        """Take the oldest job of this worker's kinds that is due, and mark it running.

        Read out as plain values rather than handed on as an ORM object: the handler runs
        in sessions of its own, and a `Job` attached to this one would be a detached
        instance the moment this transaction closes.

        The claim is the only thing separating the two workers, and it is what makes the
        un-locked read-then-write below safe: `SWEEPS` and `REACTIVE` partition every
        possible `kind` value between them, so this `SELECT` cannot return a row the other
        one is about to claim.
        """
        async with self.context.sessions() as session:
            job = await session.scalar(
                select(Job)
                .where(
                    Job.status == JobStatus.PENDING,
                    Job.run_after <= self._now(),
                    self.claim.clause(),
                )
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
                    "%s: job %d (%s) abandoned after %d attempts",
                    self.name,
                    job_id,
                    job.kind,
                    job.attempts,
                    extra={
                        "worker": self.name,
                        "job_id": job_id,
                        "job_kind": job.kind,
                        "attempts": job.attempts,
                    },
                )
            else:
                job.status = JobStatus.PENDING
                job.run_after = self._now() + _backoff(job.attempts)
            await session.commit()


def _backoff(attempts: int) -> timedelta:
    """Four seconds, then sixteen, then a minute or so, capped at ten minutes."""
    seconds = BACKOFF_BASE**attempts
    return min(timedelta(seconds=seconds), BACKOFF_CAP)
