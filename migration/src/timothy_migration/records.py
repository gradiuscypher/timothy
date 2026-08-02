"""The old collections, as typed values.

Mongo enforced nothing. Every ID is a string, every relationship is a pool name repeated
into another document, and the only integrity there ever was lived in application code
that checked before inserting and raced with itself while doing it. Nine years of that
is the input.

So parsing here is a filter, not a cast. A document that cannot become a valid row is
**quarantined** — kept, with the reason and the document that caused it — rather than
dropped or coerced. The report prints every one, because the alternative is an import
that silently loses a moderator's work and nobody notices until the sweep does not ban
someone.

Two conversions are decisions rather than mechanics:

* **`creator_id` of `"0"` becomes `system`.** The old bot used `"0"` for actions it took
  itself, and for every row the previous SQLite → Mongo migration wrote (see that repo's
  `bin/migration.rs`, which passes `"0"` as the author of every imported ban). That is
  exactly what :class:`~timothy_core.actors.Actor` calls `system`, and the whole reason
  it is a tagged value rather than a snowflake.
* **An empty pool description becomes `NULL`.** Mongo's `pool_desc` was not nullable and
  so "no description" was spelled `""`. Timothy's is nullable, and the distinction it
  draws is the one people mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from timothy_core.actors import Actor
from timothy_core.enums import SubscriptionLevel
from timothy_migration import dump

if TYPE_CHECKING:
    from collections.abc import Iterable

SYSTEM_CREATOR_ID: Final = "0"
"""What the old bot wrote for its own actions, and for everything the last migration
imported. Indistinguishable from a real user there; `system` here."""

MAX_POOL_NAME: Final = 64
"""`pools.name` is `String(64)`. SQLite would not enforce it, which is precisely why it
is checked before the insert rather than left to the database."""


@dataclass(frozen=True, slots=True)
class Rejection:
    """A document that could not become a row, and why.

    Attributes:
        collection: which Mongo collection it came from.
        reason: what was wrong, in the words the report prints.
        document: the document itself, so the operator can go and look at it.
    """

    collection: str
    reason: str
    document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SourcePool:
    """A `banpools` document."""

    name: str
    description: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SourceListing:
    """A `bans` document. A listing here — it asserts, it does not act (CONTEXT.md)."""

    user_id: int
    pool_name: str
    reason: str
    created_by: Actor
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SourceSubscription:
    """A `subscriptions` document."""

    guild_id: int
    pool_name: str
    level: SubscriptionLevel
    created_by: Actor
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SourceException:
    """An `exceptions` document."""

    guild_id: int
    user_id: int
    created_by: Actor
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SourceNotificationChannel:
    """A `notifications` document."""

    guild_id: int
    channel_id: int
    created_by: Actor
    created_at: datetime


@dataclass(slots=True)
class Source:
    """Everything readable in the dump, plus everything that was not."""

    pools: list[SourcePool] = field(default_factory=list)
    listings: list[SourceListing] = field(default_factory=list)
    subscriptions: list[SourceSubscription] = field(default_factory=list)
    exceptions: list[SourceException] = field(default_factory=list)
    notification_channels: list[SourceNotificationChannel] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """How many documents parsed, per collection, for the report."""
        return {
            dump.BANPOOLS: len(self.pools),
            dump.BANS: len(self.listings),
            dump.SUBSCRIPTIONS: len(self.subscriptions),
            dump.EXCEPTIONS: len(self.exceptions),
            dump.NOTIFICATIONS: len(self.notification_channels),
        }


class _Reader:
    """Field access that records why a document was unusable instead of raising."""

    def __init__(self, collection: str, document: dict[str, Any]) -> None:
        self.collection = collection
        self.document = document
        self.problem: str | None = None

    def fail(self, reason: str) -> None:
        """Record the first thing that was wrong. Later ones are consequences."""
        if self.problem is None:
            self.problem = reason

    def text(self, key: str, *, required: bool = True) -> str:
        """A string field, stripped."""
        value = self.document.get(key)
        if not isinstance(value, str):
            self.fail(f"{key} is {type(value).__name__}, expected a string")
            return ""
        stripped = value.strip()
        if required and not stripped:
            self.fail(f"{key} is empty")
        return stripped

    def snowflake(self, key: str) -> int:
        """A Discord ID, written as a string of digits.

        Zero is rejected: it is not a snowflake, it is the old bot's null, and letting
        one through would make guild 0 or user 0 a row that nothing can ever match.
        """
        raw = self.document.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool):
            digits = str(raw)
        elif isinstance(raw, str):
            digits = raw.strip()
        else:
            self.fail(f"{key} is {type(raw).__name__}, expected a Discord ID")
            return 0

        if not digits.isdigit():
            self.fail(f"{key} is not a Discord ID: {digits!r}")
            return 0
        value = int(digits)
        if value == 0:
            self.fail(f"{key} is 0, which is not a Discord ID")
        return value

    def actor(self, key: str) -> Actor:
        """A creator, with the old bot's `"0"` read as Timothy itself."""
        raw = self.document.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool):
            raw = str(raw)
        if raw is None or (isinstance(raw, str) and raw.strip() in {"", SYSTEM_CREATOR_ID}):
            return Actor.system()
        if not isinstance(raw, str):
            self.fail(f"{key} is {type(raw).__name__}, expected a Discord ID")
            return Actor.system()
        digits = raw.strip()
        if not digits.isdigit():
            self.fail(f"{key} is not a Discord ID: {digits!r}")
            return Actor.system()
        return Actor.user(int(digits))

    def timestamp(self, key: str) -> datetime:
        """A BSON date, as an aware UTC datetime.

        `bson` decodes dates naive-in-UTC, and Timothy's column type rejects naive
        datetimes outright rather than guessing an offset — so the tag is attached here,
        where the fact that Mongo stored UTC is actually known.

        A document with no usable date is not rejected for it. The old `add_pool` and
        friends always set one, but the field is decoration on rows whose value is the
        relationship they record; losing a listing because its date is unreadable would
        be the wrong trade. It falls back to the epoch, which is visibly not a real date.
        """
        value = self.document.get(key)
        if not isinstance(value, datetime):
            return datetime.fromtimestamp(0, tz=UTC)
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def read(source_dump: dump.Dump) -> Source:
    """Parse every collection the dump holds."""
    parsed = Source()
    _read_pools(source_dump, parsed)
    _read_listings(source_dump, parsed)
    _read_subscriptions(source_dump, parsed)
    _read_exceptions(source_dump, parsed)
    _read_notification_channels(source_dump, parsed)
    return parsed


def _documents(
    source_dump: dump.Dump, collection: str
) -> Iterable[tuple[_Reader, dict[str, Any]]]:
    for document in source_dump.documents(collection):
        yield _Reader(collection, document), document


def _keep(parsed: Source, reader: _Reader) -> bool:
    if reader.problem is None:
        return True
    parsed.rejected.append(
        Rejection(collection=reader.collection, reason=reader.problem, document=reader.document)
    )
    return False


def _read_pools(source_dump: dump.Dump, parsed: Source) -> None:
    for reader, _ in _documents(source_dump, dump.BANPOOLS):
        name = reader.text("pool_name")
        if len(name) > MAX_POOL_NAME:
            reader.fail(f"pool_name is {len(name)} characters, longer than {MAX_POOL_NAME}")
        description = reader.text("pool_desc", required=False)
        created_at = reader.timestamp("timestamp")
        if _keep(parsed, reader):
            parsed.pools.append(
                SourcePool(name=name, description=description or None, created_at=created_at)
            )


def _read_listings(source_dump: dump.Dump, parsed: Source) -> None:
    for reader, _ in _documents(source_dump, dump.BANS):
        user_id = reader.snowflake("user_id")
        pool_name = reader.text("pool_name")
        # A listing with no reason is usable; a listing with no pool is not. The reason
        # is what a moderator reads in the ban audit log, and an empty one there is worse
        # copy than "no reason recorded" but is still the truth about the old row.
        reason = reader.text("reason", required=False)
        created_by = reader.actor("creator_id")
        created_at = reader.timestamp("timestamp")
        if _keep(parsed, reader):
            parsed.listings.append(
                SourceListing(
                    user_id=user_id,
                    pool_name=pool_name,
                    reason=reason,
                    created_by=created_by,
                    created_at=created_at,
                )
            )


def _read_subscriptions(source_dump: dump.Dump, parsed: Source) -> None:
    for reader, _ in _documents(source_dump, dump.SUBSCRIPTIONS):
        guild_id = reader.snowflake("server_id")
        pool_name = reader.text("pool_name")
        level = _level(reader)
        created_by = reader.actor("creator_id")
        created_at = reader.timestamp("timestamp")
        if _keep(parsed, reader):
            parsed.subscriptions.append(
                SourceSubscription(
                    guild_id=guild_id,
                    pool_name=pool_name,
                    level=level,
                    created_by=created_by,
                    created_at=created_at,
                )
            )


def _level(reader: _Reader) -> SubscriptionLevel:
    """Read `subscription_level`, which Mongo held as a free string.

    Rejected rather than defaulted when it is neither `ban` nor `warn`. Defaulting to
    `ban` would ban people on the strength of a typo, and defaulting to `warn` would
    quietly stop enforcing a pool the guild believes it is enforcing. Neither is a
    decision an import gets to make on a moderator's behalf.
    """
    raw = reader.text("subscription_level").lower()
    try:
        return SubscriptionLevel(raw)
    except ValueError:
        reader.fail(f"subscription_level is neither ban nor warn: {raw!r}")
        return SubscriptionLevel.WARN


def _read_exceptions(source_dump: dump.Dump, parsed: Source) -> None:
    for reader, _ in _documents(source_dump, dump.EXCEPTIONS):
        guild_id = reader.snowflake("server_id")
        user_id = reader.snowflake("user_id")
        created_by = reader.actor("creator_id")
        created_at = reader.timestamp("timestamp")
        if _keep(parsed, reader):
            parsed.exceptions.append(
                SourceException(
                    guild_id=guild_id,
                    user_id=user_id,
                    created_by=created_by,
                    created_at=created_at,
                )
            )


def _read_notification_channels(source_dump: dump.Dump, parsed: Source) -> None:
    for reader, _ in _documents(source_dump, dump.NOTIFICATIONS):
        guild_id = reader.snowflake("server_id")
        channel_id = reader.snowflake("channel_id")
        created_by = reader.actor("author_id")
        created_at = reader.timestamp("timestamp")
        if _keep(parsed, reader):
            parsed.notification_channels.append(
                SourceNotificationChannel(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    created_by=created_by,
                    created_at=created_at,
                )
            )
