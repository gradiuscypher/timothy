from timothy_core.enforcement.decisions import PoolListing
from timothy_core.enforcement.messages import (
    BAN_AUDIT_REASON_LIMIT,
    BAN_COLOUR,
    WARN_COLOUR,
    ban_audit_reason,
    ban_notice,
    warn_notice,
)

GLOBAL = PoolListing(pool_id=1, pool_name="global", reason="raiding")
NUISANCE = PoolListing(pool_id=2, pool_name="nuisance", reason="spam")


def test_the_warning_makes_the_counterfactual_obvious() -> None:
    """A moderator who reads this as "we banned someone" is the failure mode."""
    notice = warn_notice(user_id=42, listing=GLOBAL)

    assert "no action taken" in notice.title
    assert "<@42>" in notice.body
    assert "still in your server" in notice.body
    assert "would have been removed" in notice.body


def test_the_warning_names_the_pool_the_reason_and_the_way_out() -> None:
    notice = warn_notice(user_id=42, listing=GLOBAL)

    assert "**global**" in notice.body
    assert "raiding" in notice.body
    assert "`/add_subscription global ban`" in notice.body
    assert "won't be warned about this user again" in notice.body


def test_a_warning_is_yellow_and_a_ban_is_red() -> None:
    """The colour is the part a moderator reads before the words."""
    assert warn_notice(user_id=42, listing=GLOBAL).colour == WARN_COLOUR
    assert ban_notice(user_id=42, justifications=[GLOBAL]).colour == BAN_COLOUR
    assert WARN_COLOUR != BAN_COLOUR


def test_the_ban_notice_names_every_pool_that_justified_it() -> None:
    notice = ban_notice(user_id=42, justifications=[GLOBAL, NUISANCE])

    assert "<@42>" in notice.body
    assert "**global**" in notice.body
    assert "raiding" in notice.body
    assert "**nuisance**" in notice.body
    assert "spam" in notice.body


def test_a_ban_reason_names_the_pool_that_caused_it() -> None:
    assert ban_audit_reason([GLOBAL]) == "Timothy: listed in global (raiding)"


def test_a_ban_reason_names_every_pool() -> None:
    reason = ban_audit_reason([GLOBAL, NUISANCE])

    assert reason == "Timothy: listed in global (raiding); nuisance (spam)"


def test_a_ban_reason_is_truncated_before_discord_does_it() -> None:
    wordy = PoolListing(pool_id=1, pool_name="global", reason="x" * 1000)

    reason = ban_audit_reason([wordy])

    assert len(reason) == BAN_AUDIT_REASON_LIMIT
    assert reason.endswith("…")
