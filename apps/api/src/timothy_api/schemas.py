"""What the API accepts and returns.

The one thing here that is not obvious: **snowflakes cross the wire as strings.** A
Discord ID is a 64-bit integer and today's are around 1.4e18, well past the 2^53 where
JavaScript's numbers stop being exact — a guild ID parsed by the browser as JSON would
come back a different guild. Discord's own API returns them as strings for this reason,
and so does Timothy's. They stay `int` everywhere inside the process.
"""

from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

from timothy_api.usernames import MAX_NAME
from timothy_core.db.models import (
    AuditLogEntry,
    EnforcementOutcome,
    Guild,
    GuildException,
    GuildRole,
    Job,
    Listing,
    NotificationChannel,
    Pool,
    Subscription,
)
from timothy_core.enforcement.diagnosis import BanBlocker
from timothy_core.enums import JobStatus, OutcomeStatus, SubscriptionLevel

SNOWFLAKE_PATTERN = r"^\d{1,20}$"

MAX_REASON = 1000
"""How much free text a human may attach to a listing, a bulk listing or an exception.

Sized from what happens downstream rather than from what SQLite would tolerate. A reason
is copied into a `Notice` Discord will reject over
:data:`~timothy_core.enforcement.messages.NOTICE_BODY_LIMIT` characters, truncated into a
ban's audit reason at 512, and — for a bulk listing — repeated into every listing row,
every audit row and every job the request creates. A thousand characters is far more than
"raid wave, 2026-08-01" needs and comfortably clear of all three.
"""

MAX_DESCRIPTION = 1000
"""How much prose a pool may carry. Never reaches Discord; bounded so that a field the UI
renders cannot be made arbitrarily large."""

MAX_ROLES = 500
"""How many roles one diagnostics report may carry. Discord's own ceiling is 250, so this
is headroom rather than a limit anybody meets — it is here so a malformed report cannot
ask the backend to write an unbounded number of rows in one transaction."""


def _to_snowflake(value: object) -> object:
    """Accept the string form the wire uses, and the int form tests and paths use."""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


Snowflake = Annotated[
    int,
    BeforeValidator(_to_snowflake),
    Field(gt=0),
    PlainSerializer(str, return_type=str),
    WithJsonSchema({"type": "string", "pattern": SNOWFLAKE_PATTERN}),
]

ActorRef = Annotated[
    str,
    BeforeValidator(str),
    WithJsonSchema({"type": "string", "examples": ["user:242024455190577152", "system"]}),
]
"""An actor rendered the way it is stored: `user:<snowflake>` or `system`."""


class PoolRead(BaseModel):
    """A pool as the API reports it."""

    id: int
    name: str
    description: str | None
    created_by: ActorRef
    created_at: datetime

    @classmethod
    def of(cls, pool: Pool) -> Self:
        """Render a stored pool."""
        return cls.model_validate(
            {
                "id": pool.id,
                "name": pool.name,
                "description": pool.description,
                "created_by": pool.created_by,
                "created_at": pool.created_at,
            }
        )


class PoolCreate(BaseModel):
    """Create a pool."""

    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)


class PoolUpdate(BaseModel):
    """Rename a pool, or change its description, or both.

    Renaming is web-only in the product (PLAN.md), but that is a decision about which
    slash commands exist, not about the API.
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)


class ListingRead(BaseModel):
    """A listing, carrying its pool's name so a caller need not join for it."""

    id: int
    pool_id: int
    pool_name: str
    user_id: Snowflake
    reason: str
    created_by: ActorRef
    created_at: datetime

    @classmethod
    def of(cls, listing: Listing, pool: Pool) -> Self:
        """Render a stored listing against the pool that carries it."""
        return cls.model_validate(
            {
                "id": listing.id,
                "pool_id": listing.pool_id,
                "pool_name": pool.name,
                "user_id": listing.user_id,
                "reason": listing.reason,
                "created_by": listing.created_by,
                "created_at": listing.created_at,
            }
        )


class ListingCreate(BaseModel):
    """Add a user to a pool. An assertion, not an action — see CONTEXT.md."""

    user_id: Snowflake
    reason: str = Field(min_length=1, max_length=MAX_REASON)


class ListingPage(BaseModel):
    """One page of listings, and how to ask for the next.

    Keyset pagination, like the audit log: `after_id` is the last id on this page, and
    the table's ids only ever grow. An offset would shift under a reader every time
    somebody added a listing, which on a pool with thousands of them is every few
    seconds during a bulk import.
    """

    listings: list[ListingRead]
    next_after_id: int | None
    """Pass back as `after_id` for the following page. `None` on the last page."""

    total: int
    """How many listings match, ignoring the page. What the UI puts above the table."""


class BulkListingCreate(BaseModel):
    """Add many users to one pool in a single call.

    Bounded, and not generously: a bulk listing is the operation that most deserves to be
    reviewed before it is sent, and every entry becomes enforcement across every
    subscribing guild.

    This bound is the *only* ceiling on the route. ADR 0007's circuit breaker does not
    apply — its budget is per guild per run, and one listing costs one action per guild,
    so a batch of any size passes under it. See `create_listings` for why that is accepted
    rather than fixed, and treat this number as load-bearing if the limit is ever raised.
    """

    reason: str = Field(min_length=1, max_length=MAX_REASON)
    """One reason for the whole batch. Bulk listing is "these accounts, this raid"."""

    user_ids: list[Snowflake] = Field(min_length=1, max_length=500)


class BulkListingDelete(BaseModel):
    """Remove many listings from one pool in a single call."""

    user_ids: list[Snowflake] = Field(min_length=1, max_length=500)


class BulkResult(BaseModel):
    """What a bulk operation did, per user.

    Partial success is reported rather than rolled back. A caller who asked for five
    hundred listings and gave three that were already there wants the four hundred and
    ninety-seven, and wants to be told about the three.
    """

    applied: list[Snowflake]
    skipped: list[Snowflake]
    """Already listed, for a create; not listed, for a delete. Not an error either way."""


class GuildRead(BaseModel):
    """A guild Timothy is in."""

    guild_id: Snowflake
    name: str | None
    """What the gateway last said it was called, or `None` if it has not said yet. A
    caller displaying this falls back to the ID, which is the identity."""

    joined_at: datetime
    enforcement_paused: bool

    @classmethod
    def of(cls, guild: Guild) -> Self:
        """Render a stored guild."""
        return cls.model_validate(
            {
                "guild_id": guild.guild_id,
                "name": guild.name,
                "joined_at": guild.joined_at,
                "enforcement_paused": guild.enforcement_paused,
            }
        )


class GuildRegister(BaseModel):
    """What the bot knows about a guild it has just seen on the gateway.

    Only the name, and even that is optional: registration has to keep working for a
    caller that sends no body at all, because that is what every deployed bot older than
    this field does.
    """

    name: str | None = Field(default=None, max_length=100)


class GuildUpdate(BaseModel):
    """Pause or resume enforcement in one guild (ADR 0007's per-guild rail)."""

    enforcement_paused: bool


class SubscriptionRead(BaseModel):
    """A guild's decision to enforce a pool."""

    guild_id: Snowflake
    pool_id: int
    pool_name: str
    level: SubscriptionLevel
    created_by: ActorRef
    created_at: datetime

    @classmethod
    def of(cls, subscription: Subscription, pool: Pool) -> Self:
        """Render a stored subscription against the pool it names."""
        return cls.model_validate(
            {
                "guild_id": subscription.guild_id,
                "pool_id": subscription.pool_id,
                "pool_name": pool.name,
                "level": subscription.level,
                "created_by": subscription.created_by,
                "created_at": subscription.created_at,
            }
        )


class SubscriptionSet(BaseModel):
    """Subscribe, or change the level of an existing subscription."""

    level: SubscriptionLevel


class ExceptionRead(BaseModel):
    """A guild's declaration that a user is never to be banned by Timothy there."""

    guild_id: Snowflake
    user_id: Snowflake
    reason: str | None
    created_by: ActorRef
    created_at: datetime

    @classmethod
    def of(cls, exception: GuildException) -> Self:
        """Render a stored exception."""
        return cls.model_validate(
            {
                "guild_id": exception.guild_id,
                "user_id": exception.user_id,
                "reason": exception.reason,
                "created_by": exception.created_by,
                "created_at": exception.created_at,
            }
        )


class ExceptionCreate(BaseModel):
    """Vouch for a user in a guild. Guild-wide, never scoped to one pool (ADR 0006)."""

    reason: str | None = Field(default=None, max_length=MAX_REASON)


class NotificationChannelRead(BaseModel):
    """Where Timothy reports what it did in a guild."""

    guild_id: Snowflake
    channel_id: Snowflake
    created_by: ActorRef
    created_at: datetime

    @classmethod
    def of(cls, channel: NotificationChannel) -> Self:
        """Render a stored notification channel."""
        return cls.model_validate(
            {
                "guild_id": channel.guild_id,
                "channel_id": channel.channel_id,
                "created_by": channel.created_by,
                "created_at": channel.created_at,
            }
        )


class NotificationChannelSet(BaseModel):
    """Point a guild's notifications at a channel."""

    channel_id: Snowflake


class EnforcementOutcomeRead(BaseModel):
    """What Timothy did about one listing in one guild, and when.

    Durable state rather than a log (ADR 0005): this is the record that makes a ban
    attributable to Timothy, and so the record that makes reverting it safe.
    """

    guild_id: Snowflake
    user_id: Snowflake
    pool_id: int
    status: OutcomeStatus
    reason: str | None
    attempted_at: datetime

    @classmethod
    def of(cls, outcome: EnforcementOutcome) -> Self:
        """Render a stored outcome."""
        return cls.model_validate(
            {
                "guild_id": outcome.guild_id,
                "user_id": outcome.user_id,
                "pool_id": outcome.pool_id,
                "status": outcome.status,
                "reason": outcome.reason,
                "attempted_at": outcome.attempted_at,
            }
        )


class RoleRead(BaseModel):
    """One of a guild's roles, as the gateway last saw it (ADR 0016)."""

    role_id: Snowflake
    name: str
    position: int
    member_count: int | None
    """How many people hold it, or `None` when the bot could not count. Never zero as a
    stand-in for "we don't know" — see :class:`~timothy_core.db.models.GuildRole`."""

    managed: bool

    @classmethod
    def of(cls, role: GuildRole) -> Self:
        """Render a stored role."""
        return cls.model_validate(
            {
                "role_id": role.role_id,
                "name": role.name,
                "position": role.position,
                "member_count": role.member_count,
                "managed": role.managed,
            }
        )


class RoleReport(BaseModel):
    """One role, as the bot reports it. The write side of :class:`RoleRead`."""

    role_id: Snowflake
    name: str = Field(max_length=100)
    position: int
    member_count: int | None = Field(default=None, ge=0)
    managed: bool = False


class DiagnosticsReport(BaseModel):
    """What the bot saw of one guild, read out of the gateway's own cache.

    Every field is a fact about Discord that Timothy only observes. Nothing here is a
    decision, which is why the whole payload can be replaced on every round without
    anybody losing anything they set.
    """

    can_ban: bool
    is_administrator: bool
    top_role_position: int = Field(ge=0)
    top_role_name: str | None = Field(default=None, max_length=100)
    owner_id: Snowflake
    member_counts_complete: bool = True
    roles: list[RoleReport] = Field(default_factory=list, max_length=MAX_ROLES)


class GuildDiagnosticsRead(BaseModel):
    """Whether Timothy can do its job in one guild, and what it cannot reach.

    A guild the bot has never reported on answers 404 rather than a row of defaults. An
    all-clear nobody measured is the one answer worse than no answer, and there is no
    honest value for `can_ban` to take when nothing has looked.
    """

    guild_id: Snowflake
    observed_at: datetime
    stale: bool
    """Whether the snapshot is older than twice the reporting interval — the bot is down,
    or cannot reach the backend."""

    can_ban: bool
    is_administrator: bool
    top_role_position: int
    top_role_name: str | None
    member_counts_complete: bool
    unbannable_roles: list[RoleRead]
    """Roles at or above Timothy's own, highest first. Nobody holding one can ever be
    banned here, whatever any pool says."""

    unbannable_members: int | None
    """People in reach of no ban, summed over the roles above — `None` when any count is
    missing. Double-counts anyone holding more than one such role, which is why the UI
    says "up to"."""


class BanFailureRead(BaseModel):
    """One ban Timothy tried to issue here and could not."""

    user_id: Snowflake
    pool_id: int
    pool_name: str | None
    """`None` for a pool that has since been deleted; the failure outlives it, because
    `enforcement_outcomes` holds no foreign keys."""

    reason: str | None
    attempted_at: datetime


class BanFailureDiagnosis(BaseModel):
    """Why one ban failed, resolved against Discord now rather than when it failed.

    Live on purpose. Somebody reading this is about to go and change a role, and the
    question they are really asking is "would it work today" — an explanation frozen at
    the moment of failure would keep naming a conflict they had already fixed.
    """

    user_id: Snowflake
    blocker: BanBlocker
    blocking_roles: list[RoleRead]
    """The target's own roles at or above Timothy's, highest first. The list of things to
    move. Empty unless `blocker` is `outranked`."""

    timothy_top_role_position: int
    timothy_top_role_name: str | None
    detail: str | None
    """What Discord said when it refused, carried through untouched."""


class DiagnosticsRefreshAck(BaseModel):
    """A one-off refresh has been asked for.

    Asked for, not done: the backend cannot reach the bot, so this records the request
    and the bot picks it up on its next poll (ADR 0016). The caller polls the snapshot to
    see it land.
    """

    guild_id: Snowflake
    requested: bool


class PendingDiagnostics(BaseModel):
    """The guilds someone has asked the bot to re-check. Draining, so reading is taking."""

    guild_ids: list[Snowflake]


class InventoryCounts(BaseModel):
    """How much of everything there is. One `COUNT(*)` each."""

    guilds: int
    guilds_paused: int
    pools: int
    listings: int
    subscriptions: int
    exceptions: int
    notification_channels: int


class QueueDepth(BaseModel):
    """The state of the job table, which is the state of enforcement.

    `sweep_outstanding` is the number of guilds with an `enforce_guild` job still to run.
    A round takes about two days against the migrated data (PLAN.md, "What a sweep
    costs"), so this counting down from the guild count is what "the sweep is working"
    looks like — and it staying flat is what a wedged worker looks like.
    """

    pending: int
    running: int
    done: int
    failed: int
    sweep_outstanding: int
    oldest_pending_at: datetime | None
    """How far behind the queue is. `None` when nothing is waiting."""


class OutcomeCounts(BaseModel):
    """Everything Timothy has recorded doing, by status, across every guild."""

    banned: int
    warned: int
    failed: int
    skipped_exception: int


class OpsOverview(BaseModel):
    """One screen's worth of "is this thing working".

    Deliberately includes the settings that decide what the numbers mean. `dry_run` in
    particular: every other figure here reads completely differently depending on it, and
    reading a zero as "nothing to do" when it is really "nothing was issued" is the
    mistake this exists to prevent.
    """

    dry_run: bool
    workers_enabled: bool
    enforcement_burst_limit: int
    sweep_interval_seconds: float
    management_guild_id: Snowflake | None
    login_configured: bool

    counts: InventoryCounts
    queue: QueueDepth
    outcomes: OutcomeCounts
    breaker_trips: int
    """Breaker trips in the activity window. Each one paused a guild until a human
    resumes it — unless it happened in dry run, which simulates the halt and skips the
    pause (ADR 0007)."""

    last_activity_at: datetime | None
    """The newest audit row. The liveness check: if Timothy is doing anything at all,
    this moves."""


class ActivityPoint(BaseModel):
    """How many of one kind of thing happened on one UTC day.

    Read from `audit_log`, which is append-only, and *not* from `enforcement_outcomes`,
    which is one row per (guild, user, pool) updated in place — grouping that by
    `attempted_at` produces a plausible chart of the wrong thing.
    """

    day: str
    """`YYYY-MM-DD`, UTC. Timothy stores UTC and does not know anybody's timezone."""

    series: str
    """The audit action, except dry-run rows, which split into
    `enforcement.dry_run:ban` and `enforcement.dry_run:warn` — during a cutover the
    difference between those two is the whole question."""

    count: int


class FailureGroup(BaseModel):
    """Enforcement that failed, gathered by guild and by cause.

    The shape the answer usually takes is "one guild, four hundred failures, all the same
    sentence" — a server that granted Timothy no ban permission. Grouping makes that one
    line instead of four hundred.
    """

    guild_id: Snowflake
    guild_name: str | None
    """`None` for a guild Timothy has since left: these rows outlive the guild row
    deliberately (see :class:`~timothy_core.db.models.EnforcementOutcome`)."""

    reason: str | None
    count: int
    latest_at: datetime


class GuildConfigSummary(BaseModel):
    """One guild's configuration, reduced to what fits on a row.

    The counts rather than the things counted: a hundred-odd guilds, each with its own
    exceptions, is a payload nobody reads and a screen nobody scans. What this is for is
    spotting the guild worth opening — the one that is paused, or subscribed to nothing,
    or has notifications pointed nowhere — and :class:`GuildConfigRead` is the opening.
    """

    guild_id: Snowflake
    name: str | None
    joined_at: datetime
    enforcement_paused: bool
    ban_subscriptions: int
    warn_subscriptions: int
    exceptions: int
    notification_channel_id: Snowflake | None
    """`None` for a guild that has nominated no channel, which is also what a warn-level
    subscription there has no way of reporting to."""


class GuildConfigRead(BaseModel):
    """Everything one guild's administrators have configured, in one call.

    The same four things `GuildDetail` assembles from four administrator-only routes, for
    a reader who administers nothing (ADR 0011). Settings only: what Timothy has *done*
    in a guild is enforcement, and what Discord will *let* it do is diagnostics, and both
    have their own routes with their own audiences.
    """

    guild: GuildRead
    subscriptions: list[SubscriptionRead]
    exceptions: list[ExceptionRead]
    notification_channel: NotificationChannelRead | None


class JobRead(BaseModel):
    """One row of the queue, as an operator needs to read it."""

    id: int
    kind: str
    payload: dict[str, object]
    run_after: datetime
    attempts: int
    status: JobStatus
    last_error: str | None
    created_at: datetime

    @classmethod
    def of(cls, job: Job) -> Self:
        """Render a stored job."""
        return cls.model_validate(
            {
                "id": job.id,
                "kind": job.kind,
                "payload": job.payload,
                "run_after": job.run_after,
                "attempts": job.attempts,
                "status": job.status,
                "last_error": job.last_error,
                "created_at": job.created_at,
            }
        )


class GatewayEvent(BaseModel):
    """Something that happened on the gateway, relayed by the bot.

    The bot has no domain logic (PLAN.md) — it says what Discord told it and nothing
    about what should follow.
    """

    guild_id: Snowflake
    user_id: Snowflake
    username: str | None = Field(default=None, max_length=MAX_NAME)
    """What Discord currently calls the user, if the event carried it.

    Optional, and ignored by every decision this event leads to. It rides along because
    the gateway already has it and the backend has no other way to learn it (ADR 0003) —
    see :mod:`timothy_api.usernames`. An older bot that does not send it relays the event
    exactly as before.
    """


class UserNameRead(BaseModel):
    """The last name Timothy saw for one user ID.

    Only IDs with a name appear in a resolution, so there is no "unknown" shape here: an
    ID that is absent from the answer has never been seen, which is a different thing
    from being called nothing.
    """

    user_id: Snowflake
    name: str
    observed_at: datetime


class EventAck(BaseModel):
    """What the backend did with a relayed event.

    Reported rather than silent so the bot can log it, and so an operator watching a
    manual unban can see whether the auto-exception fired or was suppressed.
    """

    action: str


class SignedInRead(BaseModel):
    """Who the caller is, as `/auth/me` reports it.

    Everything but `actor` and `manages_pools` is `None` for a service caller, which has
    an actor but no session and so no name, face or expiry.
    """

    actor: ActorRef
    user_id: Snowflake | None
    username: str | None
    avatar: str | None
    expires_at: datetime | None
    manages_pools: bool
    """Whether this person administers the management guild, and so owns pools and
    listings. A hint for drawing the navigation — every route resolves it again."""

    is_owner: bool
    """Whether this person is named in `TIMOTHY_OWNER_IDS`, and so may see the operations
    view (ADR 0011). Also only a hint: `/ops/*` checks it again for itself."""


class AuditLogRead(BaseModel):
    """One line of the append-only record."""

    id: int
    actor: ActorRef
    action: str
    target: str | None
    detail: dict[str, object] | None
    at: datetime

    @classmethod
    def of(cls, entry: AuditLogEntry) -> Self:
        """Render a stored audit entry."""
        return cls.model_validate(
            {
                "id": entry.id,
                "actor": entry.actor,
                "action": entry.action,
                "target": entry.target,
                "detail": entry.detail,
                "at": entry.at,
            }
        )
