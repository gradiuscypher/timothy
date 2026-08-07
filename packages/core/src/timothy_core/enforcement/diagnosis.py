"""Why Timothy cannot ban here, or cannot ban them.

Pure functions over plain values, in the shape of :mod:`~timothy_core.enforcement.decisions`
beside it: the caller gathers the state — what the gateway last saw of the guild, what
roles the target holds right now — and gets back an explanation it is responsible for
rendering. Nothing here talks to Discord or to the database.

Two of Discord's rules are the whole of this module, and both are easy to get subtly
wrong:

* **Role hierarchy is a strict inequality.** Timothy can ban a member only if its own
  highest role sits *above* the member's highest. Equal positions cannot act on each
  other, so a role at exactly Timothy's position is as unbannable as one above it. This
  is the off-by-one that makes a moderator role look fine in Discord's own list and still
  refuse every ban.
* **Nothing reaches the guild owner.** Not `ADMINISTRATOR`, not a role at the top of the
  list. Ownership is outside the hierarchy entirely, which is why it is checked separately
  rather than falling out of the position comparison.

`ADMINISTRATOR` deliberately does *not* appear here as a special case. Discord folds it
into resolved permissions, so a Timothy holding it already reports `can_ban`, and it buys
no relief at all from the hierarchy above.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Role:
    """One of a guild's roles, as the gateway last saw it."""

    role_id: int
    name: str
    position: int
    member_count: int | None = None
    """How many people hold it, or `None` when that could not be counted.

    Nullable on purpose. The count comes from the bot's member cache (ADR 0016), and a
    guild whose members were never chunked would otherwise report a confident zero — the
    one wrong answer worse than no answer, because it says the blind spot is empty.
    """

    managed: bool = False
    """Whether Discord manages it: an integration's role, a booster role, a bot's own.

    Worth separating in the UI, because the advice that fixes an ordinary unbannable role
    — move Timothy above it, or take it off the people who hold it — does not apply to a
    role no human can be granted.
    """


@dataclass(frozen=True, slots=True)
class Standing:
    """Where Timothy stands in one guild."""

    can_ban: bool
    """Whether Timothy holds `BAN_MEMBERS` here, `ADMINISTRATOR` already folded in."""

    top_role_position: int
    """The position of Timothy's own highest role. Everything at or above it is out of
    reach."""

    owner_id: int
    roles: tuple[Role, ...] = ()
    """Every role in the guild except `@everyone`, whose ID is the guild's own."""


class BanBlocker(StrEnum):
    """Why one ban did not happen.

    Ordered as :func:`diagnose` checks them, because each earlier answer subsumes the
    ones below it: a Timothy with no ban permission is not *also* usefully described as
    outranked.
    """

    NO_BAN_PERMISSION = "no_ban_permission"
    """The guild has granted Timothy no `BAN_MEMBERS`. Nothing here can be banned by
    anyone until an administrator fixes that, so no other explanation is worth giving."""

    GUILD_OWNER = "guild_owner"
    """The target owns the guild. No permission and no role position reaches them."""

    OUTRANKED = "outranked"
    """The target holds at least one role at or above Timothy's own highest."""

    LEFT_GUILD = "left_guild"
    """Nobody by that ID is in the guild now. The failure is history: enforcement is
    reactive (ADR 0004), so they are banned at the door if they ever come back."""

    UNKNOWN = "unknown"
    """None of the above explains it. Whatever Discord said at the time is all there is,
    and it is handed back verbatim rather than guessed at."""


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """One failed ban, explained."""

    blocker: BanBlocker
    blocking_roles: tuple[Role, ...] = ()
    """The target's own roles that sit at or above Timothy's, highest first. Only ever
    populated for :attr:`BanBlocker.OUTRANKED` — it is the list of things to move."""

    detail: str | None = None
    """What Discord said when the ban was refused, carried through untouched."""


def _by_position(roles: tuple[Role, ...]) -> tuple[Role, ...]:
    """Highest first, then by name so equal positions have a stable order."""
    return tuple(sorted(roles, key=lambda role: (-role.position, role.name)))


def unbannable_roles(standing: Standing) -> tuple[Role, ...]:
    """Every role nobody holding it can ever be banned, highest first.

    Purely a question about hierarchy. A Timothy with no ban permission cannot ban anyone
    at all, but that is a different problem with a different fix, and folding it in here
    would answer "every role in the guild" — true, useless, and it would hide the
    hierarchy problem again the moment the permission was granted.
    """
    return _by_position(
        tuple(role for role in standing.roles if role.position >= standing.top_role_position)
    )


def blocking_roles(standing: Standing, role_ids: frozenset[int]) -> tuple[Role, ...]:
    """The roles out of `role_ids` that outrank Timothy, highest first.

    Roles the guild no longer has are dropped rather than reported as unknown: the
    snapshot and the member lookup are taken at different moments, and a role deleted in
    between is not something to make anybody read about.
    """
    return _by_position(
        tuple(
            role
            for role in standing.roles
            if role.role_id in role_ids and role.position >= standing.top_role_position
        )
    )


def diagnose(
    *,
    standing: Standing,
    user_id: int,
    role_ids: frozenset[int] | None,
    lookup_failed: bool = False,
    detail: str | None = None,
) -> Diagnosis:
    """Explain one failed ban.

    The two verdicts that need Discord to have answered — `outranked` and `left_guild` —
    are withheld when it did not. The two that come from the stored snapshot are not:
    a Timothy with no ban permission is still a Timothy with no ban permission whether or
    not the member lookup got through, and that is the answer most worth giving.

    Args:
        standing: where Timothy stands in the guild, from the last snapshot.
        user_id: whom the ban was for.
        role_ids: the roles they hold now, or `None` if they are not in the guild at all
            — an ordinary answer rather than a failure, because enforcement is reactive
            (ADR 0004) and most listed users are somewhere else.
        lookup_failed: Discord would not say. Distinct from `role_ids=None`: "we could not
            ask" is not "they are not here", and reporting the second for the first would
            tell an administrator a problem had gone away.
        detail: what Discord said when it refused, for the case nothing else explains.
    """
    if not standing.can_ban:
        return Diagnosis(blocker=BanBlocker.NO_BAN_PERMISSION, detail=detail)
    if user_id == standing.owner_id:
        return Diagnosis(blocker=BanBlocker.GUILD_OWNER, detail=detail)
    if lookup_failed:
        return Diagnosis(blocker=BanBlocker.UNKNOWN, detail=detail)

    if role_ids is None:
        return Diagnosis(blocker=BanBlocker.LEFT_GUILD, detail=detail)

    blocking = blocking_roles(standing, role_ids)
    if blocking:
        return Diagnosis(blocker=BanBlocker.OUTRANKED, blocking_roles=blocking, detail=detail)
    return Diagnosis(blocker=BanBlocker.UNKNOWN, detail=detail)
