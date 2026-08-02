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

from timothy_core.db.models import (
    AuditLogEntry,
    EnforcementOutcome,
    Guild,
    GuildException,
    Listing,
    NotificationChannel,
    Pool,
    Subscription,
)
from timothy_core.enums import OutcomeStatus, SubscriptionLevel

SNOWFLAKE_PATTERN = r"^\d{1,20}$"


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
    description: str | None = None


class PoolUpdate(BaseModel):
    """Rename a pool, or change its description, or both.

    Renaming is web-only in the product (PLAN.md), but that is a decision about which
    slash commands exist, not about the API.
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None


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
    reason: str = Field(min_length=1)


class GuildRead(BaseModel):
    """A guild Timothy is in."""

    guild_id: Snowflake
    joined_at: datetime
    enforcement_paused: bool

    @classmethod
    def of(cls, guild: Guild) -> Self:
        """Render a stored guild."""
        return cls.model_validate(
            {
                "guild_id": guild.guild_id,
                "joined_at": guild.joined_at,
                "enforcement_paused": guild.enforcement_paused,
            }
        )


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

    reason: str | None = None


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


class GatewayEvent(BaseModel):
    """Something that happened on the gateway, relayed by the bot.

    The bot has no domain logic (PLAN.md) — it says what Discord told it and nothing
    about what should follow.
    """

    guild_id: Snowflake
    user_id: Snowflake


class EventAck(BaseModel):
    """What the backend did with a relayed event.

    Reported rather than silent so the bot can log it, and so an operator watching a
    manual unban can see whether the auto-exception fired or was suppressed.
    """

    action: str


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
