"""What Timothy says when it acts.

The copy lives in the domain because it is part of the decision, not a rendering detail:
the warn notification has to make the counterfactual obvious — nothing happened, but
something would have — or a moderator reads it as a ban and panics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

    from timothy_core.enforcement.decisions import PoolListing

BAN_AUDIT_REASON_LIMIT: Final = 512
"""Discord truncates the audit-log reason at 512 characters, so Timothy does it first
and marks where it cut."""

WARN_TEMPLATE: Final = """\
**Heads up — no action taken**
<@{user_id}> is listed in **{pool}**, which you're subscribed to at **warn** level.
They're still in your server.
**Listed for:** {reason}
Had **{pool}** been set to *ban*, they would have been removed. Switch with \
`/add_subscription {pool} ban`. You won't be warned about this user again."""


def warn_message(*, user_id: int, listing: PoolListing) -> str:
    """The notification for one warn-level match, for the guild's notification channel."""
    return WARN_TEMPLATE.format(
        user_id=user_id,
        pool=listing.pool_name,
        reason=listing.reason,
    )


def ban_audit_reason(justifications: Iterable[PoolListing]) -> str:
    """The reason Discord's audit log will show for a ban.

    Names every pool that justified it, so a moderator reading their own audit log can
    see which subscription caused this without asking Timothy.
    """
    parts = "; ".join(f"{listing.pool_name} ({listing.reason})" for listing in justifications)
    reason = f"Timothy: listed in {parts}"
    if len(reason) <= BAN_AUDIT_REASON_LIMIT:
        return reason
    return reason[: BAN_AUDIT_REASON_LIMIT - 1] + "…"
