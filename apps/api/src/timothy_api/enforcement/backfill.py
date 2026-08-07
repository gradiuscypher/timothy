"""Queuing the daily round of user-name lookups.

The names Timothy gets for free — a login, a relayed join, a relayed unban — only ever
cover people who turn up. The users on a pool were listed precisely because a guild
wanted them gone, so most of them never will, and the migrated data arrived as tens of
thousands of bare IDs. Waiting for traffic to name those is waiting forever, which is
what ADR 0017 decided to stop doing.

Shaped exactly like :mod:`timothy_api.enforcement.sweep`, and for the same reasons: it
queues a job and does no work itself, it skips a round while one is outstanding, and it
is paced by an injectable :class:`~timothy_api.enforcement.pacing.Pacer` so a test can
run two rounds rather than a wall-clock day. It lives beside the sweeper rather than
next to :mod:`timothy_api.usernames` because what it has in common with the sweeper —
the queue, the pacer, the worker that drains it — is all of its machinery; what it has
in common with `usernames` is one query.

**A round with nothing to look up queues nothing.** The backlog is finite and shrinks
every day, so most days after the first few weeks have no work in them, and a daily
no-op job would be a year of rows an operator has to read past to find a real failure.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from timothy_api import jobs, usernames
from timothy_api.enforcement.pacing import Pacer
from timothy_core.db.models import Job
from timothy_core.enums import JobStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from timothy_api.settings import Settings

OUTSTANDING = (JobStatus.PENDING, JobStatus.RUNNING)
"""A round that has not finished. Either state means today's work is already queued."""

log = logging.getLogger(__name__)


class NameBackfiller:
    """Queues a batch of user-name lookups, once per `USERNAME_BACKFILL_INTERVAL`."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        pacer: Pacer | None = None,
    ) -> None:
        """Back-fill names in `sessions`' database on `settings`' interval."""
        self.sessions = sessions
        self.settings = settings
        self.pacer = pacer if pacer is not None else Pacer()

    def stop(self) -> None:
        """Ask :meth:`run_forever` to finish after the round it is on."""
        self.pacer.stop()

    async def schedule_round(self) -> bool:
        """Queue one batch if there is one to queue. `True` if a job was added."""
        async with self.sessions() as session:
            outstanding = await session.scalar(
                select(Job.id)
                .where(
                    Job.kind == jobs.JobKind.BACKFILL_USER_NAMES.value,
                    Job.status.in_(OUTSTANDING),
                )
                .limit(1)
            )
            if outstanding is not None:
                return False

            # One row is all this asks for: the question is "is there anything at all",
            # and the job re-runs the full query when it starts anyway.
            if not await usernames.without_names(session, limit=1):
                return False

            jobs.enqueue(
                session,
                jobs.JobKind.BACKFILL_USER_NAMES,
                limit=self.settings.username_backfill_batch,
            )
            await session.commit()

        log.info(
            "queued a user name backfill of up to %d", self.settings.username_backfill_batch
        )
        return True

    async def run_forever(self) -> None:
        """Queue a round every interval, until asked to stop.

        The first round goes on the queue immediately rather than a day from now. A
        deployment that has just migrated its data is the case this exists for, and
        making somebody wait a day to see names appear would be the worst first
        impression of a feature whose whole job is recognition.
        """
        interval = self.settings.username_backfill_interval.total_seconds()
        while not self.pacer.stopping:
            try:
                await self.schedule_round()
            except Exception:
                log.exception("user name backfill scheduling failed")
            if await self.pacer.pause(interval):
                return
