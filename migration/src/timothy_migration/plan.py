"""Turning the old collections into the new rows.

A pure function: source records and a guild snapshot in, an :class:`ImportPlan` out. It
touches no database, so the interesting cases — a pool that exists twice, a subscription
to a pool that was deleted years ago, a guild that removed Timothy last March — are
cheap to write down as tests rather than as a careful reading of a script.

Four transformations here are decisions, and each of them is visible in the plan's
:attr:`~ImportPlan.anomalies` so that the report says it happened rather than the code
merely having done it.

**Pools get surrogate keys.** PLAN.md's schema references pools by ID so a pool can be
renamed without rewriting everything that points at it. Mongo referenced them by name
repeated into every document, so the plan builds the name → ID map first and rewrites
every listing and subscription against it. IDs are assigned in name order, which is
arbitrary but reproducible: the same dump twice gives the same database twice.

**Duplicates resolve the way the old bot resolved them: first write wins.** Every old
`add_*` checked for an existing document and refused the second, so a duplicate in the
dump is the residue of a race, and the row a moderator saw succeed is the earlier one.
The exception is a subscription held at two different levels, where `ban` wins — see
:func:`_subscription_level`.

**Rows for guilds Timothy is no longer in are dropped.** The `guilds` table is the
snapshot exactly, and everything cascades off it. Importing a subscription for a guild
that removed Timothy would give the sweep a guild to fail against every hour forever,
which is the noise phase 4's handoff already flags in the other direction.

**`global` is materialised.** ADR 0002 in the shape of rows: every guild in the snapshot
gets a real subscription to the auto-subscribe pool, at `ban`, unless it already has one.
Without this the guilds that never configured anything — the ones that rode the old
hardcoded short-circuit — leave the shared banlist on cutover morning without anyone
asking them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from timothy_core.actors import Actor
from timothy_core.enums import SubscriptionLevel

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime

    from timothy_migration.guilds import Snapshot
    from timothy_migration.records import (
        Source,
        SourceException,
        SourceListing,
        SourceNotificationChannel,
        SourcePool,
        SourceSubscription,
    )


class PlanError(Exception):
    """The plan cannot be built from these inputs."""


class Anomaly(StrEnum):
    """Something the plan did that the operator has to be told about.

    Not errors — every one of these is a resolved case. They are here because "the import
    ran cleanly" is worth much less than "the import ran, and here is each of the 1,412
    things it decided on your behalf".
    """

    DUPLICATE_POOL = "duplicate pool"
    DUPLICATE_LISTING = "duplicate listing"
    DUPLICATE_SUBSCRIPTION = "duplicate subscription"
    DUPLICATE_EXCEPTION = "duplicate exception"
    DUPLICATE_NOTIFICATION_CHANNEL = "duplicate notification channel"

    SUBSCRIPTION_LEVEL_CONFLICT = "subscription held at two levels"

    ORPHAN_LISTING = "listing in a pool that no longer exists"
    ORPHAN_SUBSCRIPTION = "subscription to a pool that no longer exists"

    DEPARTED_GUILD = "row for a guild Timothy is no longer in"

    GLOBAL_MATERIALISED = "global subscription materialised"


@dataclass(frozen=True, slots=True)
class Note:
    """One anomaly, with enough detail to go and look."""

    kind: Anomaly
    detail: str


@dataclass(frozen=True, slots=True)
class PlannedPool:
    """A `pools` row, with the surrogate key the rest of the plan references."""

    id: int
    name: str
    description: str | None
    created_by: Actor
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlannedListing:
    """A `listings` row."""

    user_id: int
    pool_id: int
    reason: str
    created_by: Actor
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlannedGuild:
    """A `guilds` row. Everything else in the schema cascades off one of these."""

    guild_id: int
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class PlannedSubscription:
    """A `subscriptions` row."""

    guild_id: int
    pool_id: int
    level: SubscriptionLevel
    created_by: Actor
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlannedException:
    """An `exceptions` row.

    `reason` is always `NULL`: Mongo's `BanException` had no reason field, and inventing
    one would be the import putting words in a moderator's mouth.
    """

    guild_id: int
    user_id: int
    created_by: Actor
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlannedNotificationChannel:
    """A `notification_channels` row."""

    guild_id: int
    channel_id: int
    created_by: Actor
    created_at: datetime


type PlannedRow = (
    PlannedPool
    | PlannedListing
    | PlannedGuild
    | PlannedSubscription
    | PlannedException
    | PlannedNotificationChannel
)
"""Anything the loader inserts. Every one of these is exactly its table's columns, which
is what lets :mod:`timothy_migration.load` be as short as it is."""


@dataclass(slots=True)
class ImportPlan:
    """Every row the import will write, and every decision it took to get there."""

    pools: list[PlannedPool] = field(default_factory=list)
    listings: list[PlannedListing] = field(default_factory=list)
    guilds: list[PlannedGuild] = field(default_factory=list)
    subscriptions: list[PlannedSubscription] = field(default_factory=list)
    exceptions: list[PlannedException] = field(default_factory=list)
    notification_channels: list[PlannedNotificationChannel] = field(default_factory=list)
    anomalies: list[Note] = field(default_factory=list)

    def pool_ids_by_name(self) -> dict[str, int]:
        """The name → ID map, for callers that still think in names."""
        return {pool.name: pool.id for pool in self.pools}

    def counts(self) -> dict[str, int]:
        """Rows per table, in the order PLAN.md's schema lists them."""
        return {
            "pools": len(self.pools),
            "listings": len(self.listings),
            "guilds": len(self.guilds),
            "subscriptions": len(self.subscriptions),
            "exceptions": len(self.exceptions),
            "notification_channels": len(self.notification_channels),
        }

    def anomalies_by_kind(self) -> dict[Anomaly, list[str]]:
        """The anomalies grouped for printing, in declaration order."""
        grouped: dict[Anomaly, list[str]] = {}
        for kind in Anomaly:
            details = [note.detail for note in self.anomalies if note.kind is kind]
            if details:
                grouped[kind] = details
        return grouped


def build(source: Source, snapshot: Snapshot, *, global_pool: str = "global") -> ImportPlan:
    """Plan the import.

    Args:
        source: what was readable in the dump.
        snapshot: the guilds Timothy is in, from :mod:`timothy_migration.guilds`.
        global_pool: the pool every guild is subscribed to on cutover if it is not
            already (ADR 0002). This is the backend's `TIMOTHY_AUTO_SUBSCRIBE_POOL` and
            has to match it, or a guild joining after cutover gets a different default
            from every guild that was there before. Empty disables materialisation.

    Raises:
        PlanError: `global_pool` names a pool the dump does not contain, which means
            either the wrong dump or the wrong pool name — and importing anyway would
            quietly unsubscribe every guild from the shared banlist.
    """
    plan = ImportPlan()

    _plan_pools(plan, source.pools)
    pool_ids = plan.pool_ids_by_name()

    if global_pool and global_pool not in pool_ids:
        known = ", ".join(sorted(pool_ids)) or "none"
        msg = (
            f"the dump has no pool named {global_pool!r}, so there is nothing to "
            f"subscribe guilds to (ADR 0002). Pools in the dump: {known}"
        )
        raise PlanError(msg)

    live = snapshot.guild_ids
    _plan_guilds(plan, snapshot)
    _plan_listings(plan, source.listings, pool_ids)
    _plan_subscriptions(plan, source.subscriptions, pool_ids, live)
    _plan_exceptions(plan, source.exceptions, live)
    _plan_notification_channels(plan, source.notification_channels, live)

    if global_pool:
        _materialise_global(plan, pool_ids[global_pool], global_pool, snapshot)

    return plan


# -- pools -------------------------------------------------------------------


def _plan_pools(plan: ImportPlan, pools: Sequence[SourcePool]) -> None:
    """Assign surrogate keys, in name order so the same dump gives the same IDs.

    `created_by` is `system` for every pool. Mongo's `BanPool` recorded no author at all,
    and `system` is the honest reading of "Timothy has this pool and nobody alive knows
    who asked for it" — where a snowflake would be a guess attributed to a real person.
    """
    kept: dict[str, SourcePool] = {}
    for pool in sorted(pools, key=lambda item: (item.created_at, item.name)):
        existing = kept.get(pool.name)
        if existing is None:
            kept[pool.name] = pool
            continue
        plan.anomalies.append(
            Note(
                kind=Anomaly.DUPLICATE_POOL,
                detail=(
                    f"{pool.name!r} appears more than once; kept the one created "
                    f"{existing.created_at:%Y-%m-%d}"
                ),
            )
        )

    for index, name in enumerate(sorted(kept), start=1):
        pool = kept[name]
        plan.pools.append(
            PlannedPool(
                id=index,
                name=pool.name,
                description=pool.description,
                created_by=Actor.system(),
                created_at=pool.created_at,
            )
        )


# -- guilds ------------------------------------------------------------------


def _plan_guilds(plan: ImportPlan, snapshot: Snapshot) -> None:
    """One row per guild in the snapshot, and none for any other guild.

    `joined_at` is when the snapshot was taken, which is the only thing actually known:
    Discord's guild listing does not carry the bot's join date, and Mongo never recorded
    one. It is a lower bound on nothing and an upper bound on the join, and it is at
    least not a fiction with a plausible-looking date on it.
    """
    plan.guilds.extend(
        PlannedGuild(guild_id=guild_id, joined_at=snapshot.fetched_at)
        for guild_id in sorted(snapshot.guild_ids)
    )


# -- listings ----------------------------------------------------------------


def _plan_listings(
    plan: ImportPlan, listings: Sequence[SourceListing], pool_ids: dict[str, int]
) -> None:
    """Rewrite every listing's pool name to a pool ID, dropping the orphans.

    Orphans are expected in quantity. The old `delete_pool` deleted one document and
    nothing else — no cascade, no cleanup — so every pool ever deleted left its bans
    behind, invisible to every command and enforced by nothing.
    """
    kept: dict[tuple[int, str], SourceListing] = {}
    for listing in sorted(listings, key=lambda item: (item.created_at, item.user_id)):
        if listing.pool_name not in pool_ids:
            plan.anomalies.append(
                Note(
                    kind=Anomaly.ORPHAN_LISTING,
                    detail=f"user {listing.user_id} in pool {listing.pool_name!r}",
                )
            )
            continue
        key = (listing.user_id, listing.pool_name)
        if key in kept:
            plan.anomalies.append(
                Note(
                    kind=Anomaly.DUPLICATE_LISTING,
                    detail=f"user {listing.user_id} in pool {listing.pool_name!r}",
                )
            )
            continue
        kept[key] = listing

    plan.listings.extend(
        PlannedListing(
            user_id=listing.user_id,
            pool_id=pool_ids[listing.pool_name],
            reason=listing.reason,
            created_by=listing.created_by,
            created_at=listing.created_at,
        )
        for listing in sorted(
            kept.values(), key=lambda item: (item.user_id, pool_ids[item.pool_name])
        )
    )


# -- subscriptions -----------------------------------------------------------


def _plan_subscriptions(
    plan: ImportPlan,
    subscriptions: Sequence[SourceSubscription],
    pool_ids: dict[str, int],
    live: frozenset[int],
) -> None:
    grouped: dict[tuple[int, str], list[SourceSubscription]] = defaultdict(list)

    for subscription in subscriptions:
        if subscription.guild_id not in live:
            plan.anomalies.append(
                Note(
                    kind=Anomaly.DEPARTED_GUILD,
                    detail=(
                        f"subscription: guild {subscription.guild_id} to pool "
                        f"{subscription.pool_name!r}"
                    ),
                )
            )
            continue
        if subscription.pool_name not in pool_ids:
            plan.anomalies.append(
                Note(
                    kind=Anomaly.ORPHAN_SUBSCRIPTION,
                    detail=(
                        f"guild {subscription.guild_id} to pool {subscription.pool_name!r}"
                    ),
                )
            )
            continue
        grouped[subscription.guild_id, subscription.pool_name].append(subscription)

    for (guild_id, pool_name), group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda item: item.created_at)
        first = ordered[0]
        if len(ordered) > 1:
            plan.anomalies.append(
                Note(
                    kind=Anomaly.DUPLICATE_SUBSCRIPTION,
                    detail=f"guild {guild_id} to pool {pool_name!r}, {len(ordered)} times",
                )
            )
        plan.subscriptions.append(
            PlannedSubscription(
                guild_id=guild_id,
                pool_id=pool_ids[pool_name],
                level=_subscription_level(plan, guild_id, pool_name, ordered),
                created_by=first.created_by,
                created_at=first.created_at,
            )
        )


def _subscription_level(
    plan: ImportPlan,
    guild_id: int,
    pool_name: str,
    ordered: Sequence[SourceSubscription],
) -> SubscriptionLevel:
    """Resolve one guild's level for one pool, where the dump gives more than one.

    `ban` wins, against the "first write wins" rule used everywhere else here, because
    what the old bot *did* is the thing being preserved and the old bot banned. Its live
    ban check — `is_user_banned_on_guild`, called on every member join — never read
    `subscription_level` at all: it asked whether the guild was subscribed and banned if
    it was. Only the offline `tools.rs` sync ever looked at the level.

    So a guild whose dump holds both a `ban` row and a `warn` row for one pool has been
    getting bans, and importing the earlier `warn` would stop that without telling
    anyone. It is reported either way; this is a case for a human to look at, and the
    only question here is which way to be wrong while they do.
    """
    levels = {subscription.level for subscription in ordered}
    if len(levels) == 1:
        return ordered[0].level

    plan.anomalies.append(
        Note(
            kind=Anomaly.SUBSCRIPTION_LEVEL_CONFLICT,
            detail=(
                f"guild {guild_id} holds pool {pool_name!r} at both ban and warn; "
                f"kept ban, which is what the old bot enforced"
            ),
        )
    )
    return SubscriptionLevel.BAN


def _materialise_global(
    plan: ImportPlan, pool_id: int, pool_name: str, snapshot: Snapshot
) -> None:
    """Give every guild without one a real subscription to the auto-subscribe pool.

    At `ban`, because that is what the old short-circuit did: `is_guild_subscribed`
    returned true for the name `global` unconditionally, and the join-time ban check
    acted on that without consulting a level. A guild that already has a row keeps it,
    including a `warn` one somebody set deliberately.
    """
    already = {
        subscription.guild_id
        for subscription in plan.subscriptions
        if subscription.pool_id == pool_id
    }
    materialised = sorted(snapshot.guild_ids - already)

    plan.subscriptions.extend(
        PlannedSubscription(
            guild_id=guild_id,
            pool_id=pool_id,
            level=SubscriptionLevel.BAN,
            created_by=Actor.system(),
            created_at=snapshot.fetched_at,
        )
        for guild_id in materialised
    )
    if materialised:
        plan.anomalies.append(
            Note(
                kind=Anomaly.GLOBAL_MATERIALISED,
                detail=(
                    f"{len(materialised):,} "
                    f"{'guild' if len(materialised) == 1 else 'guilds'} "
                    f"subscribed to {pool_name!r} at ban, preserving the old hardcoded "
                    f"behaviour (ADR 0002)"
                ),
            )
        )
    plan.subscriptions.sort(key=lambda item: (item.guild_id, item.pool_id))


# -- exceptions and notification channels ------------------------------------


def _plan_exceptions(
    plan: ImportPlan, exceptions: Sequence[SourceException], live: frozenset[int]
) -> None:
    kept: dict[tuple[int, int], SourceException] = {}
    for exception in _live_only(plan, exceptions, live, Anomaly.DEPARTED_GUILD, "exception"):
        key = (exception.guild_id, exception.user_id)
        if key in kept:
            plan.anomalies.append(
                Note(
                    kind=Anomaly.DUPLICATE_EXCEPTION,
                    detail=f"guild {exception.guild_id}, user {exception.user_id}",
                )
            )
            continue
        kept[key] = exception

    plan.exceptions.extend(
        PlannedException(
            guild_id=exception.guild_id,
            user_id=exception.user_id,
            created_by=exception.created_by,
            created_at=exception.created_at,
        )
        for _, exception in sorted(kept.items())
    )


def _plan_notification_channels(
    plan: ImportPlan,
    channels: Sequence[SourceNotificationChannel],
    live: frozenset[int],
) -> None:
    kept: dict[int, SourceNotificationChannel] = {}
    for channel in _live_only(
        plan, channels, live, Anomaly.DEPARTED_GUILD, "notification channel"
    ):
        if channel.guild_id in kept:
            plan.anomalies.append(
                Note(
                    kind=Anomaly.DUPLICATE_NOTIFICATION_CHANNEL,
                    detail=f"guild {channel.guild_id}",
                )
            )
            continue
        kept[channel.guild_id] = channel

    plan.notification_channels.extend(
        PlannedNotificationChannel(
            guild_id=channel.guild_id,
            channel_id=channel.channel_id,
            created_by=channel.created_by,
            created_at=channel.created_at,
        )
        for _, channel in sorted(kept.items())
    )


def _live_only[T: SourceException | SourceNotificationChannel](
    plan: ImportPlan,
    rows: Sequence[T],
    live: frozenset[int],
    kind: Anomaly,
    label: str,
) -> Iterable[T]:
    """Yield the rows whose guild Timothy is still in, oldest first, noting the rest."""
    for row in sorted(rows, key=lambda item: item.created_at):
        if row.guild_id not in live:
            plan.anomalies.append(Note(kind=kind, detail=f"{label}: guild {row.guild_id}"))
            continue
        yield row
