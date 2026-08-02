"""The decision table, case by case. This is the phase-1 deliverable that matters."""

import pytest

from timothy_core.enforcement.decisions import (
    Ban,
    EnforcementRequest,
    GuildEnforcementState,
    PoolListing,
    RevertVerdict,
    Skip,
    SkipReason,
    Warn,
    decide,
    decide_revert,
    should_except_after_unban,
)
from timothy_core.enums import SubscriptionLevel

GUILD = 100
USER = 200

GLOBAL = PoolListing(pool_id=1, pool_name="global", reason="raiding")
NUISANCE = PoolListing(pool_id=2, pool_name="nuisance", reason="spam")
UNSUBSCRIBED = PoolListing(pool_id=3, pool_name="someone-elses", reason="unrelated")


def _request(  # noqa: PLR0913 — one knob per input the decision takes
    *,
    listings: tuple[PoolListing, ...] = (GLOBAL,),
    subscriptions: dict[int, SubscriptionLevel] | None = None,
    paused: bool = False,
    present: bool = True,
    has_exception: bool = False,
    already_warned: frozenset[int] = frozenset(),
) -> EnforcementRequest:
    return EnforcementRequest(
        user_id=USER,
        guild=GuildEnforcementState(
            guild_id=GUILD,
            subscriptions=subscriptions
            if subscriptions is not None
            else {1: SubscriptionLevel.BAN},
            enforcement_paused=paused,
        ),
        listings=listings,
        user_is_present=present,
        has_exception=has_exception,
        already_warned_pool_ids=already_warned,
    )


# -- acting ------------------------------------------------------------------


def test_a_ban_level_subscription_bans() -> None:
    assert decide(_request()) == Ban(justifications=(GLOBAL,))


def test_a_warn_level_subscription_warns() -> None:
    decision = decide(_request(subscriptions={1: SubscriptionLevel.WARN}))

    assert decision == Warn(justifications=(GLOBAL,))


def test_every_ban_level_pool_is_recorded_not_just_the_first() -> None:
    """Reverting asks whether another listing still holds the ban up (ADR 0005), and it
    can only ask that of pools it recorded at the time."""
    decision = decide(
        _request(
            listings=(GLOBAL, NUISANCE),
            subscriptions={1: SubscriptionLevel.BAN, 2: SubscriptionLevel.BAN},
        ),
    )

    assert decision == Ban(justifications=(GLOBAL, NUISANCE))


def test_ban_beats_warn() -> None:
    """The warn copy promises the user is still in the server. Once they are banned it
    would be a lie, so the warning is not posted at all."""
    decision = decide(
        _request(
            listings=(GLOBAL, NUISANCE),
            subscriptions={1: SubscriptionLevel.WARN, 2: SubscriptionLevel.BAN},
        ),
    )

    assert decision == Ban(justifications=(NUISANCE,))


def test_pools_the_guild_does_not_subscribe_to_are_invisible() -> None:
    decision = decide(
        _request(listings=(UNSUBSCRIBED,), subscriptions={1: SubscriptionLevel.BAN})
    )

    assert decision == Skip(reason=SkipReason.NOT_LISTED)


def test_the_global_pool_is_an_ordinary_pool() -> None:
    """ADR 0002: no reserved names, no short circuit. Unsubscribing really unsubscribes."""
    decision = decide(_request(listings=(GLOBAL,), subscriptions={}))

    assert decision == Skip(reason=SkipReason.NOT_LISTED)


# -- warn dedupe -------------------------------------------------------------


def test_a_user_is_warned_about_once_per_pool() -> None:
    decision = decide(
        _request(subscriptions={1: SubscriptionLevel.WARN}, already_warned=frozenset({1})),
    )

    assert decision == Skip(reason=SkipReason.ALREADY_WARNED)


def test_a_second_pool_still_warns() -> None:
    decision = decide(
        _request(
            listings=(GLOBAL, NUISANCE),
            subscriptions={1: SubscriptionLevel.WARN, 2: SubscriptionLevel.WARN},
            already_warned=frozenset({1}),
        ),
    )

    assert decision == Warn(justifications=(NUISANCE,))


def test_switching_a_warned_pool_to_ban_still_bans() -> None:
    """PLAN.md: the next sweep picks up members who are still present."""
    decision = decide(
        _request(subscriptions={1: SubscriptionLevel.BAN}, already_warned=frozenset({1})),
    )

    assert decision == Ban(justifications=(GLOBAL,))


# -- skipping ----------------------------------------------------------------


def test_a_paused_guild_decides_nothing() -> None:
    assert decide(_request(paused=True)) == Skip(reason=SkipReason.ENFORCEMENT_PAUSED)


def test_an_absent_user_is_not_banned_pre_emptively() -> None:
    """ADR 0004: enforcement is reactive. They are banned at the door if they join."""
    assert decide(_request(present=False)) == Skip(reason=SkipReason.USER_ABSENT)


def test_an_exception_stops_a_ban() -> None:
    assert decide(_request(has_exception=True)) == Skip(reason=SkipReason.EXCEPTION)


def test_an_exception_stops_a_warning_too() -> None:
    """The warn copy says a ban would have happened; the exception says it never will."""
    decision = decide(_request(subscriptions={1: SubscriptionLevel.WARN}, has_exception=True))

    assert decision == Skip(reason=SkipReason.EXCEPTION)


def test_an_exception_is_guild_wide_not_per_pool() -> None:
    """ADR 0006 kept this, holes and all: one vouch excuses every pool."""
    decision = decide(
        _request(
            listings=(GLOBAL, NUISANCE),
            subscriptions={1: SubscriptionLevel.BAN, 2: SubscriptionLevel.BAN},
            has_exception=True,
        ),
    )

    assert decision == Skip(reason=SkipReason.EXCEPTION)


def test_nothing_at_all_is_a_skip_not_a_crash() -> None:
    assert decide(_request(listings=(), subscriptions={})) == Skip(reason=SkipReason.NOT_LISTED)


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        pytest.param(
            _request(paused=True, listings=(), present=False, has_exception=True),
            SkipReason.ENFORCEMENT_PAUSED,
            id="paused outranks everything",
        ),
        pytest.param(
            _request(listings=(), present=False, has_exception=True),
            SkipReason.NOT_LISTED,
            id="not listed outranks absence",
        ),
        pytest.param(
            _request(present=False, has_exception=True),
            SkipReason.USER_ABSENT,
            id="absence outranks the exception",
        ),
    ],
)
def test_skip_precedence(request_: EnforcementRequest, expected: SkipReason) -> None:
    """The order matters because a recorded skip changes what happens next time: only
    the exception is durable, and only when it is the actual reason."""
    assert decide(request_) == Skip(reason=expected)


# -- reverting ---------------------------------------------------------------


def test_a_guilds_own_ban_is_never_lifted() -> None:
    verdict = decide_revert(banned_by_timothy=False, still_justified=False)

    assert verdict is RevertVerdict.NOT_ATTRIBUTABLE


def test_attribution_is_checked_before_justification() -> None:
    verdict = decide_revert(banned_by_timothy=False, still_justified=True)

    assert verdict is RevertVerdict.NOT_ATTRIBUTABLE


def test_another_live_listing_holds_the_ban_up() -> None:
    verdict = decide_revert(banned_by_timothy=True, still_justified=True)

    assert verdict is RevertVerdict.STILL_JUSTIFIED


def test_timothys_own_ban_with_nothing_left_to_justify_it_is_lifted() -> None:
    verdict = decide_revert(banned_by_timothy=True, still_justified=False)

    assert verdict is RevertVerdict.REVERT


# -- auto-exceptions ---------------------------------------------------------


def test_a_manual_unban_of_a_listed_user_becomes_an_exception() -> None:
    """Otherwise the next sweep re-bans them within the hour."""
    assert should_except_after_unban(
        unban_was_timothys_own=False,
        listed_in_subscribed_pool=True,
    )


def test_a_manual_unban_of_an_unlisted_user_is_a_no_op() -> None:
    """The old bot hooked every unban and filled the exception list with strangers."""
    assert not should_except_after_unban(
        unban_was_timothys_own=False,
        listed_in_subscribed_pool=False,
    )


@pytest.mark.parametrize("listed", [True, False])
def test_timothys_own_unban_never_creates_an_exception(*, listed: bool) -> None:
    """ADR 0005: a revert that exempted the users it just readmitted would make the
    next enforcement of that listing a no-op forever."""
    assert not should_except_after_unban(
        unban_was_timothys_own=True,
        listed_in_subscribed_pool=listed,
    )
