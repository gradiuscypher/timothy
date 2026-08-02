from timothy_core.enforcement.decisions import PoolListing
from timothy_core.enforcement.messages import (
    BAN_AUDIT_REASON_LIMIT,
    ban_audit_reason,
    warn_message,
)

GLOBAL = PoolListing(pool_id=1, pool_name="global", reason="raiding")
NUISANCE = PoolListing(pool_id=2, pool_name="nuisance", reason="spam")


def test_the_warning_makes_the_counterfactual_obvious() -> None:
    """A moderator who reads this as "we banned someone" is the failure mode."""
    message = warn_message(user_id=42, listing=GLOBAL)

    assert "no action taken" in message
    assert "<@42>" in message
    assert "still in your server" in message
    assert "would have been removed" in message


def test_the_warning_names_the_pool_the_reason_and_the_way_out() -> None:
    message = warn_message(user_id=42, listing=GLOBAL)

    assert "**global**" in message
    assert "raiding" in message
    assert "`/add_subscription global ban`" in message
    assert "won't be warned about this user again" in message


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
