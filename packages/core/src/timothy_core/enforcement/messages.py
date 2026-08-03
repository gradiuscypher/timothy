"""What Timothy says when it acts.

The copy lives in the domain because it is part of the decision, not a rendering detail:
the warn notification has to make the counterfactual obvious — nothing happened, but
something would have — or a moderator reads it as a ban and panics.

The colour belongs here for the same reason. Yellow for a warn and red for a ban is the
distinction above, restated in the one part of an embed that is read before any of the
words. What Discord does with the number is the adapter's business.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from timothy_core.ports.discord import Notice

if TYPE_CHECKING:
    from collections.abc import Iterable

    from timothy_core.enforcement.decisions import PoolListing

BAN_AUDIT_REASON_LIMIT: Final = 512
"""Discord truncates the audit-log reason at 512 characters, so Timothy does it first
and marks where it cut."""

WARN_COLOUR: Final = 0xFEE75C
"""Discord's own yellow. A warn is the colour of something to look at, not something
that happened."""

BAN_COLOUR: Final = 0xED4245
"""Discord's own red, for the one action Timothy takes that removes a person."""

WARN_TITLE: Final = "Heads up — no action taken"

WARN_TEMPLATE: Final = """\
<@{user_id}> is listed in **{pool}**, which you're subscribed to at **warn** level.
They're still in your server.
**Listed for:** {reason}
Had **{pool}** been set to *ban*, they would have been removed. Switch with \
`/add_subscription {pool} ban`. You won't be warned about this user again."""

BAN_TITLE: Final = "User banned"

BAN_TEMPLATE: Final = """\
<@{user_id}> has been banned from this server.
**Listed in:** {pools}
Lift it with Discord's own unban if this was wrong — Timothy will not reissue it."""


def warn_notice(*, user_id: int, listing: PoolListing) -> Notice:
    """The notice for one warn-level match, for the guild's notification channel."""
    return Notice(
        title=WARN_TITLE,
        body=WARN_TEMPLATE.format(
            user_id=user_id,
            pool=listing.pool_name,
            reason=listing.reason,
        ),
        colour=WARN_COLOUR,
    )


def ban_notice(*, user_id: int, justifications: Iterable[PoolListing]) -> Notice:
    """The notice for a ban Timothy issued, for the guild's notification channel.

    Names every pool that justified it, as the audit reason does, so the channel and the
    audit log tell the same story.
    """
    pools = "\n".join(
        f"• **{listing.pool_name}** — {listing.reason}" for listing in justifications
    )
    return Notice(
        title=BAN_TITLE,
        body=BAN_TEMPLATE.format(user_id=user_id, pools=pools),
        colour=BAN_COLOUR,
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
