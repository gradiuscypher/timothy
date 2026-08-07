"""Storing what the bot saw, and asking it to look again.

The backend cannot see any of this for itself. Role positions, and above all how many
people hold a role, are not things Discord's REST API will answer cheaply — but the bot
already holds every one of them in the gateway's own member cache, for free (ADR 0016).
So the bot observes on a timer and this stores what it says.

Which leaves one gap: the backend has no way to *reach* the bot, and a person who has
just fixed a role in Discord should not have to wait out the timer. :class:`RefreshQueue`
is that gap closed from the other side — an administrator's request is recorded here and
the bot collects it on its next poll.

In-process and not a table, for the same reasons as
:class:`~timothy_api.enforcement.selfunbans.SelfUnbans`: losing it across a restart costs
nothing, because the next scheduled round reports every guild anyway.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from timothy_core.db.models import GuildDiagnostics, GuildRole
from timothy_core.enforcement.diagnosis import Role, Standing

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import timedelta

    from sqlalchemy.ext.asyncio import AsyncSession

    from timothy_api.schemas import DiagnosticsReport


class RefreshQueue:
    """Guilds an administrator has asked the bot to re-check.

    A set rather than a list: pressing the button twice while the bot is between polls is
    one re-check, not two, and the guild that gets checked is the same either way.
    """

    def __init__(self) -> None:
        """Start with nothing outstanding."""
        self._guild_ids: set[int] = set()

    def request(self, guild_id: int) -> None:
        """Ask for this guild to be re-checked on the bot's next poll."""
        self._guild_ids.add(guild_id)

    def drain(self) -> list[int]:
        """Take everything outstanding, leaving the queue empty.

        Draining on read rather than on acknowledgement is deliberate. A request the bot
        collected and then failed to act on is lost, and that is the right trade: the
        cost is one stale snapshot until the next scheduled round, while a queue that
        needed acknowledging would grow without bound whenever the bot was down.
        """
        taken = sorted(self._guild_ids)
        self._guild_ids.clear()
        return taken


async def record(
    session: AsyncSession, *, guild_id: int, report: DiagnosticsReport
) -> GuildDiagnostics:
    """Replace everything Timothy knows about this guild's shape.

    Wholesale, not merged. Every row here is a cache of Discord's own state, so a role
    the guild has deleted must stop being reported rather than linger as a blocker nobody
    can find in their own settings. Nothing commits — the caller owns the transaction, as
    it does for :func:`timothy_api.audit.record`.
    """
    stored = await session.get(GuildDiagnostics, guild_id)
    if stored is None:
        stored = GuildDiagnostics(guild_id=guild_id)
        session.add(stored)

    stored.can_ban = report.can_ban
    stored.is_administrator = report.is_administrator
    stored.top_role_position = report.top_role_position
    stored.top_role_name = report.top_role_name
    stored.owner_id = report.owner_id
    stored.member_counts_complete = report.member_counts_complete
    stored.observed_at = datetime.now(UTC)

    await session.execute(delete(GuildRole).where(GuildRole.guild_id == guild_id))
    session.add_all(
        GuildRole(
            guild_id=guild_id,
            role_id=role.role_id,
            name=role.name,
            position=role.position,
            member_count=role.member_count,
            managed=role.managed,
        )
        for role in report.roles
    )
    return stored


async def read(
    session: AsyncSession, guild_id: int
) -> tuple[GuildDiagnostics, list[GuildRole]] | None:
    """The stored snapshot and its roles, or `None` if the bot has never reported."""
    stored = await session.get(GuildDiagnostics, guild_id)
    if stored is None:
        return None
    roles = list(await session.scalars(select(GuildRole).where(GuildRole.guild_id == guild_id)))
    return stored, roles


def standing_of(stored: GuildDiagnostics, roles: Iterable[GuildRole]) -> Standing:
    """The domain's view of a stored snapshot, ready for :mod:`diagnosis`."""
    return Standing(
        can_ban=stored.can_ban,
        top_role_position=stored.top_role_position,
        owner_id=stored.owner_id,
        roles=tuple(
            Role(
                role_id=role.role_id,
                name=role.name,
                position=role.position,
                member_count=role.member_count,
                managed=role.managed,
            )
            for role in roles
        ),
    )


def is_stale(stored: GuildDiagnostics, interval: timedelta, *, now: datetime) -> bool:
    """Whether the snapshot has gone unrefreshed for longer than it should have.

    Twice the interval, not once. A round is staggered across the whole interval on
    purpose, so the guild that happens to be reported last is legitimately almost a full
    interval old, and a threshold of one would have half the fleet permanently warning.
    """
    return now - stored.observed_at > interval * 2


def unbannable_members(roles: Sequence[Role]) -> int | None:
    """How many people are out of reach, or `None` if any role could not be counted.

    Double-counts anybody holding more than one unbannable role. Deduplicating would need
    the member lists themselves, which is exactly the data this design keeps in the bot
    and out of the database — so the figure is reported as a ceiling and the UI says so.
    """
    if any(role.member_count is None for role in roles):
        return None
    return sum(role.member_count or 0 for role in roles)
