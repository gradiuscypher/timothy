"""Is this thing working?

The operator's view, and the only part of the API that is about Timothy itself rather
than about pools, guilds or people. Everything here is read-only: nothing on this router
changes anything, which is what makes it safe to leave a dashboard polling it.

**Where the numbers come from matters.** Anything counted over time is read from
`audit_log`, which is append-only. `enforcement_outcomes` is one row per
(guild, user, pool) updated in place — its `attempted_at` is the *latest* attempt, not a
history, so grouping it by day would draw a confident chart of something that is not
true. Outcomes are counted here only as totals, which is what that table can honestly
answer.

Gated on the management guild's administrators, the same as the audit log. They are the
people who own the pools, which is as close to "the operator" as ADR 0001's derived model
gets — and inventing an owner list here would be the first authority Timothy stored
rather than derived.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from timothy_api import audit
from timothy_api.deps import Requires, SessionDep, SettingsDep
from timothy_api.jobs import JobKind
from timothy_api.policy import Operation
from timothy_api.schemas import (
    ActivityPoint,
    FailureGroup,
    InventoryCounts,
    JobRead,
    OpsOverview,
    OutcomeCounts,
    QueueDepth,
)
from timothy_core.actors import Actor
from timothy_core.db.models import (
    AuditLogEntry,
    EnforcementOutcome,
    Guild,
    GuildException,
    Job,
    Listing,
    NotificationChannel,
    Pool,
    Subscription,
)
from timothy_core.enums import JobStatus, OutcomeStatus

router = APIRouter(prefix="/ops", tags=["ops"])

Operator = Annotated[Actor, Depends(Requires(Operation.READ_OPS))]

Days = Annotated[
    int,
    Query(ge=1, le=90, description="How many days back to count. UTC days."),
]
Limit = Annotated[int, Query(ge=1, le=200, description="How many rows to return.")]
Before = Annotated[
    int | None,
    Query(gt=0, description="Return jobs older than this id. Omit for the newest page."),
]
StatusFilter = Annotated[
    JobStatus | None,
    Query(description="Only jobs with this status. `failed` is the interesting one."),
]
KindFilter = Annotated[
    str | None, Query(description="Only jobs of this kind, e.g. `enforce_guild`.")
]

DEFAULT_DAYS = 14

DRY_RUN_WOULD = "$.would"
"""Where the engine puts what a dry-run row *would* have done — `ban` or `warn`. During
a cutover that distinction is the whole question, so the activity series splits on it."""


async def _count(session: AsyncSession, *whereclause: ColumnElement[bool], of: type) -> int:
    """`SELECT COUNT(*)`, and never `None`."""
    total = await session.scalar(select(func.count()).select_from(of).where(*whereclause))
    return total or 0


@router.get("/overview")
async def read_overview(
    _actor: Operator,
    session: SessionDep,
    settings: SettingsDep,
    days: Days = DEFAULT_DAYS,
) -> OpsOverview:
    """Everything a dashboard's top half needs, in one call.

    One round trip rather than eight, because these are read together or not at all, and
    a screen that renders half its tiles is worse than one that renders none.

    The settings come back with the counts on purpose. `dry_run` in particular decides
    what every other number on this page *means*: a zero in `outcomes.banned` reads as
    "nothing needed doing" when dry run is off and "nothing was issued" when it is on,
    and those are opposite situations.
    """
    since = datetime.now(UTC) - timedelta(days=days)

    counts = InventoryCounts(
        guilds=await _count(session, of=Guild),
        guilds_paused=await _count(session, Guild.enforcement_paused.is_(True), of=Guild),
        pools=await _count(session, of=Pool),
        listings=await _count(session, of=Listing),
        subscriptions=await _count(session, of=Subscription),
        exceptions=await _count(session, of=GuildException),
        notification_channels=await _count(session, of=NotificationChannel),
    )

    queue = QueueDepth(
        pending=await _count(session, Job.status == JobStatus.PENDING, of=Job),
        running=await _count(session, Job.status == JobStatus.RUNNING, of=Job),
        done=await _count(session, Job.status == JobStatus.DONE, of=Job),
        failed=await _count(session, Job.status == JobStatus.FAILED, of=Job),
        sweep_outstanding=await _count(
            session,
            Job.kind == JobKind.ENFORCE_GUILD.value,
            Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            of=Job,
        ),
        oldest_pending_at=await session.scalar(
            select(func.min(Job.created_at)).where(Job.status == JobStatus.PENDING)
        ),
    )

    outcomes = OutcomeCounts(
        banned=await _count(
            session, EnforcementOutcome.status == OutcomeStatus.BANNED, of=EnforcementOutcome
        ),
        warned=await _count(
            session, EnforcementOutcome.status == OutcomeStatus.WARNED, of=EnforcementOutcome
        ),
        failed=await _count(
            session, EnforcementOutcome.status == OutcomeStatus.FAILED, of=EnforcementOutcome
        ),
        skipped_exception=await _count(
            session,
            EnforcementOutcome.status == OutcomeStatus.SKIPPED_EXCEPTION,
            of=EnforcementOutcome,
        ),
    )

    return OpsOverview(
        dry_run=settings.dry_run,
        workers_enabled=settings.workers_enabled,
        enforcement_burst_limit=settings.enforcement_burst_limit,
        sweep_interval_seconds=settings.sweep_interval.total_seconds(),
        management_guild_id=settings.management_guild_id or None,
        login_configured=bool(
            settings.discord_client_id
            and settings.discord_client_secret.get_secret_value()
            and settings.public_base_url
        ),
        counts=counts,
        queue=queue,
        outcomes=outcomes,
        breaker_trips=await _count(
            session,
            AuditLogEntry.action == audit.AuditAction.ENFORCEMENT_BREAKER_TRIPPED.value,
            AuditLogEntry.at >= since,
            of=AuditLogEntry,
        ),
        last_activity_at=await session.scalar(select(func.max(AuditLogEntry.at))),
    )


@router.get("/activity")
async def read_activity(
    _actor: Operator, session: SessionDep, days: Days = DEFAULT_DAYS
) -> list[ActivityPoint]:
    """What happened, per UTC day, per kind of thing.

    Grouped in SQL rather than pulled into Python: a busy day is thousands of audit rows,
    and the window is up to ninety of them.

    Days with nothing in them are absent rather than zero. A caller drawing a chart
    should fill the gaps itself — the API reporting zeroes it never observed would be
    inventing rows in an append-only record.
    """
    since = datetime.now(UTC) - timedelta(days=days)

    day = func.date(AuditLogEntry.at)
    would = func.json_extract(AuditLogEntry.detail, DRY_RUN_WOULD)

    rows = await session.execute(
        select(day, AuditLogEntry.action, would, func.count())
        .where(AuditLogEntry.at >= since)
        .group_by(day, AuditLogEntry.action, would)
        .order_by(day, AuditLogEntry.action)
    )
    return [
        ActivityPoint(
            day=str(on),
            series=f"{action}:{intent}" if intent else action,
            count=count,
        )
        for on, action, intent, count in rows
    ]


@router.get("/failures")
async def read_failures(
    _actor: Operator, session: SessionDep, limit: Limit = 50
) -> list[FailureGroup]:
    """Enforcement that failed, by guild and by cause, worst first.

    The everyday shape of this is one guild and one sentence repeated: a server that
    granted Timothy no ban permission fails identically for every listed user it has.
    Grouping turns four hundred rows into one line an operator can act on.

    A `failed` outcome is not a failed job — it is a Discord call that retrying did not
    fix, which the sweep will try again when the world might have changed. Jobs are
    below, and are a different problem.
    """
    rows = await session.execute(
        select(
            EnforcementOutcome.guild_id,
            EnforcementOutcome.reason,
            func.count(),
            func.max(EnforcementOutcome.attempted_at),
        )
        .where(EnforcementOutcome.status == OutcomeStatus.FAILED)
        .group_by(EnforcementOutcome.guild_id, EnforcementOutcome.reason)
        .order_by(func.count().desc())
        .limit(limit)
    )
    return [
        FailureGroup(guild_id=guild_id, reason=reason, count=count, latest_at=latest)
        for guild_id, reason, count, latest in rows
    ]


@router.get("/jobs")
async def read_jobs(
    _actor: Operator,
    session: SessionDep,
    limit: Limit = 50,
    before_id: Before = None,
    status: StatusFilter = None,
    kind: KindFilter = None,
) -> list[JobRead]:
    """The queue itself, newest first.

    Read-only, and there is deliberately no way to retry from here. A job reaches `failed`
    only after exhausting its attempts on something running it again would not fix — an
    unknown kind, a payload missing the key its handler needs. The failures that *are*
    worth retrying are recorded as enforcement outcomes instead, and the sweep picks
    those up on its own (see :mod:`timothy_api.enforcement.engine`). A retry button here
    would be a button that reliably does nothing.
    """
    query = select(Job).order_by(Job.id.desc()).limit(limit)
    if before_id is not None:
        query = query.where(Job.id < before_id)
    if status is not None:
        query = query.where(Job.status == status)
    if kind is not None:
        query = query.where(Job.kind == kind)

    return [JobRead.of(job) for job in await session.scalars(query)]
