"""What should happen to this user, in this guild, right now.

Pure functions over plain values. The caller gathers the state — the user's live
listings, the guild's subscriptions and exceptions, what has already been enforced —
and gets back a decision it is then responsible for carrying out. Nothing here talks to
Discord or to the database, which is what makes the interesting cases cheap to test.

## Precedence

The order the skips are checked in is a decision in itself, because a skip is recorded
as an enforcement outcome and a recorded outcome changes what happens next time:

1. **Paused** — the guild is switched off (ADR 0007's per-guild rail). Nothing is
   decided and nothing is recorded, so resuming enforces normally.
2. **Not listed** — no pool the guild subscribes to lists this user. The overwhelmingly
   common answer, and the cheapest.
3. **Absent** — enforcement is reactive (ADR 0004): a listed user who is not in the
   guild is banned at the door if they ever join, not before. Recording nothing here is
   what leaves that door armed.
4. **Exception** — the guild has vouched for this user (ADR 0006). Worth recording, as
   `skipped_exception`, because "we deliberately did nothing" is the one skip a
   moderator will later ask about.

An exception suppresses warnings as well as bans. The definition in CONTEXT.md is about
bans, but the warn copy tells a moderator that a ban *would* have happened — which is
precisely what an exception says will never happen. Warning anyway would contradict a
decision the guild has already made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from timothy_core.enums import SubscriptionLevel

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class PoolListing:
    """A live listing, joined to the pool that carries it."""

    pool_id: int
    pool_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class GuildEnforcementState:
    """What the guild has asked for."""

    guild_id: int
    subscriptions: Mapping[int, SubscriptionLevel] = field(default_factory=dict)
    """Pool ID to the level the guild holds it at."""

    enforcement_paused: bool = False


@dataclass(frozen=True, slots=True)
class EnforcementRequest:
    """One (user, guild) question.

    `listings` is every live listing for the user across every pool — filtering to the
    ones this guild actually subscribes to is the decision's job, not the caller's.
    """

    user_id: int
    guild: GuildEnforcementState
    listings: Sequence[PoolListing] = ()
    user_is_present: bool = True
    has_exception: bool = False
    already_warned_pool_ids: frozenset[int] = frozenset()
    """Pools this user has already been warned about in this guild. A `warned` outcome
    is permanent, surviving leaves and rejoins."""


class SkipReason(StrEnum):
    """Why nothing is going to happen."""

    ENFORCEMENT_PAUSED = "enforcement_paused"
    NOT_LISTED = "not_listed"
    USER_ABSENT = "user_absent"
    EXCEPTION = "exception"
    ALREADY_WARNED = "already_warned"


@dataclass(frozen=True, slots=True)
class Ban:
    """Ban the user, and record a `banned` outcome against every pool listed here.

    All of them, not just the first: reverting asks whether any *other* live listing
    still holds the ban up (ADR 0005), and it can only ask that of pools it recorded.
    """

    justifications: tuple[PoolListing, ...]


@dataclass(frozen=True, slots=True)
class Warn:
    """Post one notification per pool, and record a `warned` outcome for each."""

    justifications: tuple[PoolListing, ...]


@dataclass(frozen=True, slots=True)
class Skip:
    """Do nothing, for this reason."""

    reason: SkipReason


type Decision = Ban | Warn | Skip


def subscribed_listings(request: EnforcementRequest) -> tuple[PoolListing, ...]:
    """The user's listings that this guild has actually subscribed to."""
    return tuple(
        listing
        for listing in request.listings
        if listing.pool_id in request.guild.subscriptions
    )


def _skip_reason(
    request: EnforcementRequest, relevant: tuple[PoolListing, ...]
) -> SkipReason | None:
    if request.guild.enforcement_paused:
        return SkipReason.ENFORCEMENT_PAUSED
    if not relevant:
        return SkipReason.NOT_LISTED
    if not request.user_is_present:
        return SkipReason.USER_ABSENT
    if request.has_exception:
        return SkipReason.EXCEPTION
    return None


def decide(request: EnforcementRequest) -> Decision:
    """Decide what to do about one user in one guild.

    A ban-level subscription beats a warn-level one: if any pool listing this user is
    held at `ban`, the user is banned and no warning is posted, because the counterfactual
    the warn copy describes is no longer counterfactual.
    """
    relevant = subscribed_listings(request)

    skip = _skip_reason(request, relevant)
    if skip is not None:
        return Skip(reason=skip)

    at_level = {
        level: tuple(
            listing
            for listing in relevant
            if request.guild.subscriptions[listing.pool_id] is level
        )
        for level in SubscriptionLevel
    }

    if at_level[SubscriptionLevel.BAN]:
        return Ban(justifications=at_level[SubscriptionLevel.BAN])

    unwarned = tuple(
        listing
        for listing in at_level[SubscriptionLevel.WARN]
        if listing.pool_id not in request.already_warned_pool_ids
    )
    if not unwarned:
        return Skip(reason=SkipReason.ALREADY_WARNED)
    return Warn(justifications=unwarned)


class RevertVerdict(StrEnum):
    """Whether a ban may be lifted now that the thing justifying it is gone."""

    REVERT = "revert"

    NOT_ATTRIBUTABLE = "not_attributable"
    """No recorded outcome says Timothy issued this ban, so it is the guild's own and
    is never touched (ADR 0005)."""

    STILL_JUSTIFIED = "still_justified"
    """Another live listing in another subscribed pool independently holds it up."""


def decide_revert(*, banned_by_timothy: bool, still_justified: bool) -> RevertVerdict:
    """Decide whether to lift one ban.

    Attribution first: without a recorded `banned` outcome Timothy has no evidence the
    ban is its own, and a guild's own bans are never lifted no matter what the listings
    say.

    Args:
        banned_by_timothy: a `banned` enforcement outcome exists for this guild and user.
        still_justified: some other live listing in a pool this guild still subscribes
            to at ban level covers this user.
    """
    if not banned_by_timothy:
        return RevertVerdict.NOT_ATTRIBUTABLE
    if still_justified:
        return RevertVerdict.STILL_JUSTIFIED
    return RevertVerdict.REVERT


def should_except_after_unban(
    *, unban_was_timothys_own: bool, listed_in_subscribed_pool: bool
) -> bool:
    """Whether a `GUILD_BAN_REMOVE` should create an exception (ADR 0006).

    The hook exists so a moderator's manual unban is not undone by the next sweep. It
    therefore fires only where the unban *would* be undone — the user is listed in a pool
    this guild subscribes to. The old bot fired on every unban and filled the exception
    list with users who were never in a pool.

    Timothy's own unbans are excluded outright: a revert that exempted the very users it
    just readmitted would make the next enforcement of that listing a no-op forever.

    Args:
        unban_was_timothys_own: the unban came from Timothy's revert path, not a human.
        listed_in_subscribed_pool: the user is listed in a pool this guild subscribes to.
    """
    if unban_was_timothys_own:
        return False
    return listed_in_subscribed_pool
