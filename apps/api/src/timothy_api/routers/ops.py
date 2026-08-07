"""Is this thing working?

The operator's view: the part of the API that is about Timothy itself rather than about
pools, guilds or people. Everything here is read-only: nothing on this router changes
anything, which is what makes it safe to leave a dashboard polling it.

`/ops/guilds` is the one thing here that reads other people's configuration, and it is
here rather than under `/guilds` because of who may read it. Every route under `/guilds`
is scoped to the caller's own guilds, by design; this one is scoped to nobody's, which
makes it an operator's route wearing a guild's subject matter. Read-only like the rest —
seeing a setting in order to explain it is not authority to change it.

**Where the numbers come from matters.** Anything counted over time is read from
`audit_log`, which is append-only. `enforcement_outcomes` is one row per
(guild, user, pool) updated in place — its `attempted_at` is the *latest* attempt, not a
history, so grouping it by day would draw a confident chart of something that is not
true. Outcomes are counted here only as totals, which is what that table can honestly
answer.

Gated on `TIMOTHY_OWNER_IDS` alone (ADR 0011). This was first gated on the management
guild's administrators, on the reasoning that they were as close to "the operator" as the
derived model got; they are not. Running the deployment and curating the pools are
different jobs, and since ADR 0012 they are not even the same permission.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from timothy_api import audit
from timothy_api.deps import Requires, SessionDep, SettingsDep
from timothy_api.jobs import JobKind
from timothy_api.lookups import get_guild
from timothy_api.policy import Operation
from timothy_api.schemas import (
    ActivityPoint,
    ExceptionRead,
    FailureGroup,
    GuildConfigRead,
    GuildConfigSummary,
    GuildRead,
    InventoryCounts,
    JobRead,
    NotificationChannelRead,
    OpsOverview,
    OutcomeCounts,
    QueueDepth,
    Snowflake,
    SubscriptionRead,
)
from timothy_api.search import MAX_QUERY, matching
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
from timothy_core.enums import JobStatus, OutcomeStatus, SubscriptionLevel

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
Search = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=MAX_QUERY,
        description=(
            "Match against the kind, the payload, or the last error as text. A user or "
            "guild ID finds the queued work about it."
        ),
    ),
]
GuildSearch = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=MAX_QUERY,
        description="Match against the guild's name or its ID, as text.",
    ),
]
GuildId = Annotated[Snowflake, Path(description="A Discord guild ID.")]

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
        # The management guild is part of this: login requires membership of it
        # (ADR 0013), so unset means `/auth/login` answers 503 like any other missing
        # credential. Reporting "configured" for a login nobody can complete would make
        # this line worse than not being here.
        login_configured=bool(
            settings.discord_client_id
            and settings.discord_client_secret.get_secret_value()
            and settings.public_base_url
            and settings.management_guild_id
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

    The guild's name comes from an outer join, because these rows deliberately outlive
    the guild row: a guild Timothy has left still has failures worth reading, and it has
    no name here.
    """
    rows = await session.execute(
        select(
            EnforcementOutcome.guild_id,
            Guild.name,
            EnforcementOutcome.reason,
            func.count(),
            func.max(EnforcementOutcome.attempted_at),
        )
        .outerjoin(Guild, Guild.guild_id == EnforcementOutcome.guild_id)
        .where(EnforcementOutcome.status == OutcomeStatus.FAILED)
        .group_by(EnforcementOutcome.guild_id, Guild.name, EnforcementOutcome.reason)
        .order_by(func.count().desc())
        .limit(limit)
    )
    return [
        FailureGroup(
            guild_id=guild_id,
            guild_name=guild_name,
            reason=reason,
            count=count,
            latest_at=latest,
        )
        for guild_id, guild_name, reason, count, latest in rows
    ]


@router.get("/guilds")
async def read_guild_configs(
    _actor: Operator, session: SessionDep, q: GuildSearch = None
) -> list[GuildConfigSummary]:
    """Every guild Timothy is in and how each one is configured, whoever is asking.

    `/guilds` answers a different question and keeps its answer: it lists the guilds the
    caller administers, because it is the front door of a web UI for administrators. The
    operator administers nothing (ADR 0011) and gets an empty list there — which is
    correct, and useless when the report is "Timothy is not banning in my server" and the
    answer turns out to be a pause nobody remembers setting.

    Unpaged on purpose. This is the deployment's inventory, bounded by how many guilds
    Timothy is in, and a page boundary through "all guild settings" would quietly answer
    a different question than the one asked.

    Four queries rather than one per guild: the aggregates are grouped in SQL and joined
    up here, because a hundred guilds each fetching their own counts is a hundred round
    trips to draw one table.
    """
    guilds = list(
        await session.scalars(
            select(Guild)
            .where(*([matching(q, Guild.name, Guild.guild_id)] if q is not None else []))
            # Nameless guilds sort last rather than first: a `NULL` name means the gateway
            # has not mentioned this guild since it was registered, which is a curiosity
            # and not the top of the list.
            .order_by(Guild.name.is_(None), Guild.name, Guild.guild_id)
        )
    )
    guild_ids = [guild.guild_id for guild in guilds]

    levels = {
        (guild_id, level): count
        for guild_id, level, count in await session.execute(
            select(Subscription.guild_id, Subscription.level, func.count())
            .where(Subscription.guild_id.in_(guild_ids))
            .group_by(Subscription.guild_id, Subscription.level)
        )
    }
    exception_counts = await session.execute(
        select(GuildException.guild_id, func.count().label("total"))
        .where(GuildException.guild_id.in_(guild_ids))
        .group_by(GuildException.guild_id)
    )
    exceptions = {row.guild_id: row.total for row in exception_counts}

    nominated = await session.execute(
        select(NotificationChannel.guild_id, NotificationChannel.channel_id).where(
            NotificationChannel.guild_id.in_(guild_ids)
        )
    )
    channels = {row.guild_id: row.channel_id for row in nominated}

    return [
        GuildConfigSummary(
            guild_id=guild.guild_id,
            name=guild.name,
            joined_at=guild.joined_at,
            enforcement_paused=guild.enforcement_paused,
            ban_subscriptions=levels.get((guild.guild_id, SubscriptionLevel.BAN), 0),
            warn_subscriptions=levels.get((guild.guild_id, SubscriptionLevel.WARN), 0),
            exceptions=exceptions.get(guild.guild_id, 0),
            notification_channel_id=channels.get(guild.guild_id),
        )
        for guild in guilds
    ]


@router.get("/guilds/{guild_id}")
async def read_guild_config(
    guild_id: GuildId, _actor: Operator, session: SessionDep
) -> GuildConfigRead:
    """One guild's settings in full, whoever is asking.

    Everything the guild's own administrators configured, assembled here so that reading
    it costs one call rather than four — this is the "why is this server behaving like
    that" screen, and the answer is usually the shape of the whole configuration rather
    than any one row of it.

    Read-only, and there is deliberately no operator write beside it. Being able to see
    a guild's settings to explain them is not authority over them: a subscription belongs
    to the guild that holds it (ADR 0001), and an operator who changes one has made a
    change its administrators never made and cannot see the reason for.
    """
    guild = await get_guild(session, guild_id)

    subscriptions = await session.execute(
        select(Subscription, Pool)
        .join(Pool, Pool.id == Subscription.pool_id)
        .where(Subscription.guild_id == guild_id)
        .order_by(Pool.name)
    )
    exceptions = await session.scalars(
        select(GuildException)
        .where(GuildException.guild_id == guild_id)
        .order_by(GuildException.created_at)
    )
    channel = await session.get(NotificationChannel, guild_id)

    return GuildConfigRead(
        guild=GuildRead.of(guild),
        subscriptions=[
            SubscriptionRead.of(subscription, pool) for subscription, pool in subscriptions
        ],
        exceptions=[ExceptionRead.of(exception) for exception in exceptions],
        notification_channel=(
            NotificationChannelRead.of(channel) if channel is not None else None
        ),
    )


@router.get("/jobs")
async def read_jobs(
    _actor: Operator,
    session: SessionDep,
    limit: Limit = 50,
    before_id: Before = None,
    status: StatusFilter = None,
    kind: KindFilter = None,
    q: Search = None,
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
    if q is not None:
        query = query.where(matching(q, Job.kind, Job.payload, Job.last_error))

    return [JobRead.of(job) for job in await session.scalars(query)]
