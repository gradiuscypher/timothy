"""The safety net, and only the safety net.

ADR 0004 demoted the sweep: enforcement is immediate and reactive, so this exists to
catch what the gateway missed while it was down, not to be the way bans happen. It
therefore does nothing itself except queue an `ENFORCE_GUILD` job per guild and let the
worker do the work — one path into enforcement, whatever provoked it.

**Staggered, using the queue's own `run_after`.** A round spreads its guilds evenly
across the interval rather than queueing them all at midnight, so the Discord calls are
spread too and no single tick is the expensive one. That needs no scheduler of its own:
the jobs are simply dated forward.

**A guild with a sweep outstanding is skipped.** Otherwise a guild slow enough to take
longer than the interval accumulates a queue it can never work off. Its outstanding job
will pick up whatever arrived in the meantime — the candidates are computed when the job
runs, not when it was queued.

Outstanding means pending *or running*, and the second half is not decoration. A guild
sweep is a `fetch_member` per candidate, and Discord paces those at a couple a second per
guild — so a guild with a few thousand listed users takes half an hour, comfortably longer
than any sensible interval. Counting only pending jobs let every such guild pick up a
second job while its first was still running, which is exactly the accumulation this is
here to prevent. Found by running a real sweep against real data, not by reading it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from timothy_api import jobs
from timothy_api.enforcement.pacing import Pacer
from timothy_core.db.models import Guild, Job
from timothy_core.enums import JobStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from timothy_api.settings import Settings

OUTSTANDING = (JobStatus.PENDING, JobStatus.RUNNING)
"""A sweep that has not finished. Either state means the guild already has one."""

log = logging.getLogger(__name__)


class Sweeper:
    """Queues a sweep of every guild, once per `SWEEP_INTERVAL`."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        pacer: Pacer | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Sweep the guilds in `sessions`' database on `settings`' interval."""
        self.sessions = sessions
        self.settings = settings
        self.pacer = pacer if pacer is not None else Pacer()
        self._now = now

    def stop(self) -> None:
        """Ask :meth:`run_forever` to finish after the round it is on."""
        self.pacer.stop()

    async def schedule_round(self) -> int:
        """Queue one round of sweeps, staggered. Returns how many were queued."""
        interval = self.settings.sweep_interval
        async with self.sessions() as session:
            guild_ids = list(
                await session.scalars(
                    select(Guild.guild_id)
                    .where(Guild.enforcement_paused.is_(False))
                    .order_by(Guild.joined_at, Guild.guild_id)
                )
            )
            already = set(
                await session.scalars(
                    select(Job.payload["guild_id"].as_integer()).where(
                        Job.kind == jobs.JobKind.ENFORCE_GUILD.value,
                        Job.status.in_(OUTSTANDING),
                    )
                )
            )

            due = [guild_id for guild_id in guild_ids if guild_id not in already]
            if not due:
                return 0

            step = interval / len(due)
            base = self._now()
            for position, guild_id in enumerate(due):
                job = jobs.enqueue(session, jobs.JobKind.ENFORCE_GUILD, guild_id=guild_id)
                job.run_after = base + step * position
            await session.commit()

        log.info("queued %d guild sweep(s) over %s", len(due), interval)
        return len(due)

    async def run_forever(self) -> None:
        """Queue a round every interval, until asked to stop. The lifespan's other task.

        The first round is queued immediately: a restart is one of the gaps the sweep
        exists to cover, and waiting an hour to find out what was missed while the
        process was down defeats the point.
        """
        interval = self.settings.sweep_interval.total_seconds()
        while not self.pacer.stopping:
            try:
                await self.schedule_round()
            except Exception:
                log.exception("sweep scheduling failed")
            if await self.pacer.pause(interval):
                return
