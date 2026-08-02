"""The two checks the cutover actually turns on.

PLAN.md's phase 5 asks for a rehearsal, not a script: "verify counts and spot-check; run
the new stack in dry run against production data and diff its intended actions against
the old bot's behaviour before switching dry run off". These are those two things.

**`verify` is static and complete.** It rebuilds, from the imported database, the set of
(guild, user) pairs Timothy would enforce against, and compares it to the same set
computed from the dump by :mod:`timothy_migration.oldbot`. It runs the real
:func:`~timothy_core.enforcement.decisions.decide` — the production function, not a
restatement of it — so what it proves is about Timothy and not about a model of Timothy.
Because it asks only about decisions and never about who is currently in a guild, it
covers every pair, including the users nobody has seen in years.

**`diff` is dynamic and partial.** It reads the audit log of a real dry run and checks
each intended action against the old rule. What it adds over `verify` is the parts
`verify` cannot reach: live guild membership, live permissions, the notification channels
actually being resolvable, the worker and the sweep really running. What it cannot show
is silence — a pair Timothy decided to skip writes nothing at all (ADR 0009), so
under-enforcement is invisible here. That is precisely the gap `verify` closes, which is
why both exist and why neither is optional.

Both classify their findings into three buckets rather than pass/fail, because two of the
differences between the old bot and Timothy are *intended* and will never go to zero:

* a guild that set a pool to `warn` was still being banned from it by the old bot's live
  check, and now genuinely warns (see :mod:`timothy_migration.oldbot`);
* a guild subscribed to a pool that `delete_pool` removed years ago was still having that
  pool's leftover bans enforced against it, and now is not.

An operator has to be able to see those two numbers, agree with them, and then look at
the bucket that should be empty.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from timothy_core.db.engine import make_engine
from timothy_core.db.models import (
    AuditLogEntry,
    Guild,
    GuildException,
    Listing,
    NotificationChannel,
    Pool,
    Subscription,
)
from timothy_core.enforcement.decisions import (
    Ban,
    EnforcementRequest,
    GuildEnforcementState,
    PoolListing,
    Warn,
    decide,
)
from timothy_migration.load import sqlite_url

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from timothy_migration.oldbot import OldBot

DRY_RUN_ACTION = "enforcement.dry_run"
"""The audit action dry run writes, and the only record it leaves (ADR 0009)."""


class Verdict(StrEnum):
    """How one (guild, user) pair compares across the cutover."""

    AGREES = "agrees"
    """Both would ban, or neither would do anything. The overwhelming majority."""

    NOW_WARNS = "now warns instead of banning"
    """The guild holds the pool at `warn`. The old bot's live check ignored the level and
    banned anyway; Timothy honours it. Intended, and the reason `warn` was worth keeping
    in the schema at all — but somebody has to agree to it per guild."""

    NO_LONGER_ENFORCED = "no longer enforced"
    """The old bot would ban and Timothy will not. Expected only where the justification
    was a listing in a pool that no longer exists — the old `delete_pool` left the bans
    behind and `is_guild_subscribed` matched the dead name. Anything else here is a bug
    in the import."""

    NEWLY_ENFORCED = "newly enforced"
    """Timothy would act where the old bot would not. Nothing in the migration is
    supposed to produce this, and a non-zero count means the import invented a
    subscription, a listing or a missing exception. Stop and look."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One pair that did not simply agree."""

    verdict: Verdict
    guild_id: int
    user_id: int
    detail: str


@dataclass(slots=True)
class Comparison:
    """The outcome of a check: what was compared, and everything that was not agreement."""

    pairs_compared: int = 0
    findings: list[Finding] = field(default_factory=list)

    def tally(self) -> dict[Verdict, int]:
        """Counts per verdict, agreement included, in declaration order."""
        counted = Counter(finding.verdict for finding in self.findings)
        counted[Verdict.AGREES] = self.pairs_compared - sum(counted.values())
        return {verdict: counted[verdict] for verdict in Verdict if counted[verdict]}

    @property
    def unexplained(self) -> list[Finding]:
        """The findings that no intended change accounts for.

        This is the list the cutover waits on. The other two verdicts are policy changes
        that were argued for in an ADR; these are the import being wrong.
        """
        return [
            finding for finding in self.findings if finding.verdict is Verdict.NEWLY_ENFORCED
        ]


@dataclass(frozen=True, slots=True)
class Imported:
    """The imported database, read back into the shape the decision logic wants.

    Read in full and held in memory. It is a few tens of thousands of rows, and the
    alternative — a query per (guild, user) pair — is a few million round trips to answer
    a question that is really one join.
    """

    pool_names: dict[int, str]
    guild_ids: tuple[int, ...]
    listings_by_user: dict[int, tuple[PoolListing, ...]]
    subscriptions: dict[int, GuildEnforcementState]
    exceptions: frozenset[tuple[int, int]]
    notification_channels: frozenset[int]

    def request(self, *, guild_id: int, user_id: int) -> EnforcementRequest:
        """The question the production decision function answers.

        `user_is_present` is `True` for every pair on purpose. The check is about who
        Timothy would enforce against, not about who happens to be online — membership is
        the same fact on both sides of the cutover, so holding it constant is what lets
        the two decision sets be compared at all.
        """
        return EnforcementRequest(
            user_id=user_id,
            guild=self.subscriptions.get(guild_id, GuildEnforcementState(guild_id=guild_id)),
            listings=self.listings_by_user.get(user_id, ()),
            user_is_present=True,
            has_exception=(guild_id, user_id) in self.exceptions,
        )


async def read_imported(path: Path) -> Imported:
    """Load the imported database into memory."""
    engine = make_engine(sqlite_url(path))
    try:
        async with engine.connect() as connection:
            pool_names = {
                row.id: row.name for row in await connection.execute(select(Pool.id, Pool.name))
            }

            listings: dict[int, list[PoolListing]] = {}
            for row in await connection.execute(
                select(Listing.user_id, Listing.pool_id, Listing.reason)
            ):
                listings.setdefault(row.user_id, []).append(
                    PoolListing(
                        pool_id=row.pool_id,
                        pool_name=pool_names.get(row.pool_id, str(row.pool_id)),
                        reason=row.reason,
                    )
                )

            levels: dict[int, dict[int, Any]] = {}
            for row in await connection.execute(
                select(Subscription.guild_id, Subscription.pool_id, Subscription.level)
            ):
                levels.setdefault(row.guild_id, {})[row.pool_id] = row.level

            guild_ids = tuple(
                sorted(row.guild_id for row in await connection.execute(select(Guild.guild_id)))
            )
            paused = {
                row.guild_id
                for row in await connection.execute(
                    select(Guild.guild_id).where(Guild.enforcement_paused)
                )
            }

            return Imported(
                pool_names=pool_names,
                guild_ids=guild_ids,
                listings_by_user={user_id: tuple(items) for user_id, items in listings.items()},
                subscriptions={
                    guild_id: GuildEnforcementState(
                        guild_id=guild_id,
                        subscriptions=subscribed,
                        enforcement_paused=guild_id in paused,
                    )
                    for guild_id, subscribed in levels.items()
                },
                exceptions=frozenset(
                    (row.guild_id, row.user_id)
                    for row in await connection.execute(
                        select(GuildException.guild_id, GuildException.user_id)
                    )
                ),
                notification_channels=frozenset(
                    row.guild_id
                    for row in await connection.execute(select(NotificationChannel.guild_id))
                ),
            )
    finally:
        await engine.dispose()


def verify(imported: Imported, old: OldBot) -> Comparison:
    """Compare every (guild, user) decision the two systems would make.

    The pairs are every guild in the imported database crossed with every user listed
    anywhere — on either side, so that a user the import lost still gets asked about.
    """
    comparison = Comparison()
    users = sorted(set(imported.listings_by_user) | set(old.pools_by_user))

    for guild_id in imported.guild_ids:
        for user_id in users:
            comparison.pairs_compared += 1
            finding = _compare(imported, old, guild_id=guild_id, user_id=user_id)
            if finding is not None:
                comparison.findings.append(finding)

    return comparison


def _compare(imported: Imported, old: OldBot, *, guild_id: int, user_id: int) -> Finding | None:
    decision = decide(imported.request(guild_id=guild_id, user_id=user_id))
    old_pools = old.justifying_pools(guild_id=guild_id, user_id=user_id)

    match decision:
        case Ban():
            if old_pools:
                return None
            return _finding(
                Verdict.NEWLY_ENFORCED,
                guild_id,
                user_id,
                f"Timothy would ban via {_names(decision.justifications)}; "
                f"the old bot would not have",
            )
        case Warn():
            if old_pools:
                return _finding(
                    Verdict.NOW_WARNS,
                    guild_id,
                    user_id,
                    f"held at warn for {_names(decision.justifications)}; "
                    f"the old bot banned via {_sorted(old_pools)}",
                )
            return _finding(
                Verdict.NEWLY_ENFORCED,
                guild_id,
                user_id,
                f"Timothy would warn via {_names(decision.justifications)}; "
                f"the old bot would not have acted",
            )
        case _:
            if not old_pools:
                return None
            return _finding(
                Verdict.NO_LONGER_ENFORCED,
                guild_id,
                user_id,
                f"the old bot banned via {_sorted(old_pools)}; {_why_not(imported, old_pools)}",
            )


def _why_not(imported: Imported, old_pools: frozenset[str]) -> str:
    """Say which of the two intended causes explains a pair Timothy no longer enforces.

    Almost always the dead-pool one: the old `delete_pool` removed a pool document and
    left its bans and its subscriptions in place, and `is_guild_subscribed` matched the
    name whether or not a pool wore it. Naming the cause per finding is what turns a
    column of numbers into something an operator can sign off.
    """
    surviving = set(imported.pool_names.values())
    gone = sorted(old_pools - surviving)
    if gone:
        return f"{', '.join(repr(name) for name in gone)} no longer exists as a pool"
    return "the guild is no longer subscribed, or the listing did not survive the import"


def _names(justifications: Sequence[PoolListing]) -> str:
    return _sorted({listing.pool_name for listing in justifications})


def _sorted(names: set[str] | frozenset[str]) -> str:
    return ", ".join(repr(name) for name in sorted(names)) or "nothing"


def _finding(verdict: Verdict, guild_id: int, user_id: int, detail: str) -> Finding:
    return Finding(verdict=verdict, guild_id=guild_id, user_id=user_id, detail=detail)


# -- the dry run diff --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Intention:
    """One `enforcement.dry_run` audit row, parsed.

    Attributes:
        guild_id: from the `guild:<id>/user:<id>` target.
        user_id: likewise.
        would: `ban` or `warn` — what the row's detail says Timothy was about to do.
    """

    guild_id: int
    user_id: int
    would: str


async def read_intentions(path: Path) -> list[Intention]:
    """Every intention a dry run recorded in `path`'s audit log.

    Rows whose target or detail is not the shape the enforcement engine writes are
    skipped rather than raising: the audit log is append-only and shared, and a check
    that fell over on an unfamiliar row would be a check that stops working the first
    time anything else is added to it.
    """
    engine = make_engine(sqlite_url(path))
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                select(AuditLogEntry.target, AuditLogEntry.detail).where(
                    AuditLogEntry.action == DRY_RUN_ACTION
                )
            )
            return [
                intention
                for row in rows
                if (intention := _intention(row.target, row.detail)) is not None
            ]
    finally:
        await engine.dispose()


def _intention(target: str | None, detail: dict[str, Any] | None) -> Intention | None:
    """Parse `guild:<id>/user:<id>` and `{"would": ...}`, per `timothy_api.audit`."""
    if not target or not isinstance(detail, dict):
        return None
    guild_part, _, user_part = target.partition("/")
    guild_id = guild_part.removeprefix("guild:")
    user_id = user_part.removeprefix("user:")
    would = detail.get("would")
    if not (guild_id.isdigit() and user_id.isdigit() and isinstance(would, str)):
        return None
    return Intention(guild_id=int(guild_id), user_id=int(user_id), would=would)


def diff(intentions: Sequence[Intention], old: OldBot) -> Comparison:
    """Classify what a dry run intended against what the old bot would have done.

    One pair per intention. Duplicates are expected and are counted separately rather
    than collapsed: dry run does not dedupe (ADR 0009), because with no outcome row
    written there is nothing to dedupe against, so every sweep restates every intention.
    Seeing the same pair three times means three sweeps, not three decisions.
    """
    comparison = Comparison()
    for intention in intentions:
        comparison.pairs_compared += 1
        would_ban = old.would_ban(guild_id=intention.guild_id, user_id=intention.user_id)
        finding = _classify(intention, would_ban=would_ban, old=old)
        if finding is not None:
            comparison.findings.append(finding)
    return comparison


def _classify(intention: Intention, *, would_ban: bool, old: OldBot) -> Finding | None:
    pools = old.justifying_pools(guild_id=intention.guild_id, user_id=intention.user_id)
    if intention.would == "ban" and would_ban:
        return None
    if intention.would == "warn" and would_ban:
        return _finding(
            Verdict.NOW_WARNS,
            intention.guild_id,
            intention.user_id,
            f"dry run would warn; the old bot banned via {_sorted(pools)}",
        )
    return _finding(
        Verdict.NEWLY_ENFORCED,
        intention.guild_id,
        intention.user_id,
        f"dry run would {intention.would}; the old bot would not have acted",
    )
