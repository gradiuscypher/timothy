"""The tables from PLAN.md's Schema section.

Two structural choices are load-bearing and deliberate:

* **Pools have a surrogate key.** The name stays unique and stays what humans type —
  slash commands and API paths resolve by name — but nothing references it, so a pool
  can be renamed without rewriting every listing and subscription.
* **`enforcement_outcomes` has no foreign keys.** It is durable state, not a log: it is
  what makes a ban attributable to Timothy and therefore revertable (ADR 0005). Cascading
  it away with a deleted pool would silently destroy the ability to revert, so the
  `guild_id`/`pool_id` there are plain columns that outlive the rows they name.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from timothy_core.actors import Actor
from timothy_core.db.columns import ActorColumn, UtcDateTime
from timothy_core.enums import JobStatus, OutcomeStatus, SubscriptionLevel

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Every constraint gets a stable name, because SQLite can only alter a table by
rebuilding it and Alembic's batch mode needs something to call the constraints."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


def _enum(enum: type[StrEnum], name: str) -> Enum:
    """A string column with a CHECK constraint, storing the enum's values.

    `native_enum=False` because SQLite has no enum type; `create_constraint=True`
    because without it the column would accept any string at all.
    """
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=_values,
    )


Snowflake = Annotated[int, mapped_column(BigInteger().with_variant(Integer, "sqlite"))]
"""A Discord ID. 64-bit everywhere; on SQLite that is what `INTEGER` already is, and
using it keeps primary keys eligible to be rowid aliases."""

CreatedAt = Annotated[datetime, mapped_column(UtcDateTime, default=_utcnow)]


class Base(DeclarativeBase):
    """Declarative base carrying the constraint naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012 — SQLAlchemy reads this as a plain dict
        datetime: UtcDateTime,
        Actor: ActorColumn,
        str: Text,
        dict[str, Any]: JSON,
    }


class Pool(Base):
    """A named, curated list of Discord users that guilds can subscribe to."""

    __tablename__ = "pools"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None]
    created_by: Mapped[Actor]
    created_at: Mapped[CreatedAt]


class Listing(Base):
    """A record that a user belongs on a pool.

    An assertion, not an action: creating one bans nobody by itself. What follows is
    enforcement, and that is recorded in `enforcement_outcomes`.
    """

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("user_id", "pool_id"),
        Index("ix_listings_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Snowflake]
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id", ondelete="CASCADE"))
    reason: Mapped[str]
    created_by: Mapped[Actor]
    created_at: Mapped[CreatedAt]


class Guild(Base):
    """A Discord server Timothy is in."""

    __tablename__ = "guilds"

    guild_id: Mapped[Snowflake] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String(100), default=None)
    """What the guild was called the last time the gateway mentioned it.

    A cache, and nullable because it can be stale or absent: a guild registered before
    this column existed has none until the bot next reconnects, and nothing may depend on
    it. The ID is the identity — this is only so a person reading the web UI recognises
    which of their servers they are looking at, without Timothy spending a Discord call
    per row to find out."""

    joined_at: Mapped[datetime] = mapped_column(default=_utcnow)
    enforcement_paused: Mapped[bool] = mapped_column(default=False)
    """The per-guild rail from ADR 0007: isolate one misbehaving guild without stopping
    the service. Also where the circuit breaker parks a guild until a human resumes it."""


class Subscription(Base):
    """A guild's decision to enforce a pool, at ban or warn level.

    `global` is an ordinary pool here — ADR 0002 dropped the reserved name, so every
    subscription including that one is a real row a guild administrator can delete.
    """

    __tablename__ = "subscriptions"

    guild_id: Mapped[Snowflake] = mapped_column(
        ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    level: Mapped[SubscriptionLevel] = mapped_column(
        _enum(SubscriptionLevel, "subscription_level"),
    )
    created_by: Mapped[Actor]
    created_at: Mapped[CreatedAt]


class GuildException(Base):
    """A guild's declaration that a user is never to be banned by Timothy there.

    Guild-wide, never scoped to one pool (ADR 0006). Named `GuildException` in Python
    only to keep clear of the builtin; the domain word is Exception.
    """

    __tablename__ = "exceptions"

    guild_id: Mapped[Snowflake] = mapped_column(
        ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    user_id: Mapped[Snowflake] = mapped_column(primary_key=True, autoincrement=False)
    reason: Mapped[str | None]
    created_by: Mapped[Actor]
    created_at: Mapped[CreatedAt]


class NotificationChannel(Base):
    """Where Timothy reports what it did in a guild. One per guild."""

    __tablename__ = "notification_channels"

    guild_id: Mapped[Snowflake] = mapped_column(
        ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    channel_id: Mapped[Snowflake]
    created_by: Mapped[Actor]
    created_at: Mapped[CreatedAt]


class GuildDiagnostics(Base):
    """What Timothy can do in one guild, as the gateway last saw it (ADR 0016).

    A cache of Discord's own state, not a decision of Timothy's — every column here is
    something an administrator changes in Discord and Timothy only observes. It therefore
    cascades away with the guild, unlike `enforcement_outcomes`: a snapshot of somewhere
    Timothy is no longer describes nothing anybody can act on.
    """

    __tablename__ = "guild_diagnostics"

    guild_id: Mapped[Snowflake] = mapped_column(
        ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    can_ban: Mapped[bool]
    """Whether Timothy holds `BAN_MEMBERS` here, `ADMINISTRATOR` already folded in."""

    is_administrator: Mapped[bool]
    """Kept beside `can_ban` because it changes the advice, not the verdict: an
    administrator that still cannot ban has a role hierarchy problem and nothing else."""

    top_role_position: Mapped[int]
    top_role_name: Mapped[str | None] = mapped_column(String(100))
    owner_id: Mapped[Snowflake]
    """Who owns the guild. Outside the role hierarchy entirely, so it is the one fact a
    position comparison can never derive."""

    member_counts_complete: Mapped[bool] = mapped_column(default=True)
    """Whether the counts in `guild_roles` can be believed.

    False when the bot's member cache was incomplete when it looked. The counts are then
    stored as NULL rather than as zero — see :class:`GuildRole.member_count`."""

    observed_at: Mapped[datetime] = mapped_column(default=_utcnow)


class GuildRole(Base):
    """One of a guild's roles, as the gateway last saw it.

    Replaced wholesale on every snapshot rather than merged, so a role the guild has
    deleted stops being reported instead of lingering as a blocker nobody can find.
    """

    __tablename__ = "guild_roles"

    guild_id: Mapped[Snowflake] = mapped_column(
        ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    role_id: Mapped[Snowflake] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int]
    member_count: Mapped[int | None]
    """How many hold it, or NULL when it could not be counted.

    Nullable deliberately. Zero is a claim that the blind spot is empty, and a guild whose
    members were never chunked would make that claim about every role it has."""

    managed: Mapped[bool] = mapped_column(default=False)
    """Discord's own: an integration's role, a booster role, a bot's. Reported separately
    because "move Timothy above it" is the only advice that applies to one."""


class EnforcementOutcome(Base):
    """The recorded result of enforcing one listing in one guild.

    One row per (guild, user, pool), updated in place: the composite primary key *is*
    the warn-dedupe key. Deliberately unconstrained by foreign keys — see the module
    docstring.
    """

    __tablename__ = "enforcement_outcomes"

    guild_id: Mapped[Snowflake] = mapped_column(primary_key=True, autoincrement=False)
    user_id: Mapped[Snowflake] = mapped_column(primary_key=True, autoincrement=False)
    pool_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    status: Mapped[OutcomeStatus] = mapped_column(
        _enum(OutcomeStatus, "outcome_status"),
    )
    reason: Mapped[str | None]
    """Why the listing said to act, or why the attempt failed."""

    attempted_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Job(Base):
    """A unit of enforcement work waiting for a worker.

    Written in the same transaction as the mutation that justifies it, so it cannot be
    committed without that mutation or run before it.
    """

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]]
    run_after: Mapped[datetime] = mapped_column(default=_utcnow)
    attempts: Mapped[int] = mapped_column(default=0)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"),
        default=JobStatus.PENDING,
    )
    last_error: Mapped[str | None] = mapped_column(default=None)
    """Why the most recent attempt failed. A job that has exhausted its attempts is the
    one an operator has to be able to read without turning on debug logging."""

    created_at: Mapped[CreatedAt]


class Session(Base):
    """A logged-in web session.

    The `id` is the SHA-256 of the token the browser holds, not the token — 64 hex
    characters, which is what the column was already sized for. A reader of this table
    therefore cannot log in as anybody: they have the digest, and the cookie is the
    preimage.

    `guild_ids` is what Discord said the user was in at login, from the OAuth `guilds`
    scope. It is a Discord-derived fact with a timestamp on it rather than a stored
    grant, and it is only ever used to narrow which guilds get asked about (ADR 0010).
    """

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[Snowflake]
    username: Mapped[str] = mapped_column(String(64), default="")
    """What to call them in the UI's corner. Saves a Discord call per page load."""

    avatar: Mapped[str | None] = mapped_column(String(64), default=None)
    """Discord's avatar hash, or `None` for the default avatar."""

    guild_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    created_at: Mapped[CreatedAt]
    expires_at: Mapped[datetime]


class AuditLogEntry(Base):
    """One append-only line: who did what to which thing, and when.

    Covers Timothy's own actions as well as people's, which is why `actor` is an
    :class:`~timothy_core.actors.Actor` and not a user ID.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_at", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[Actor]
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict[str, Any] | None]
    at: Mapped[datetime] = mapped_column(default=_utcnow)
