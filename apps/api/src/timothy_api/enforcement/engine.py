"""Doing what was decided, behind ADR 0007's three rails.

`decide()` says ban, warn or skip. This turns that into a Discord call and a durable
record of what happened, and it is where dry run, the circuit breaker and the per-guild
pause actually bite.

Three orderings here are load-bearing:

**Discord first, then the record.** A ban is issued and only then recorded. Crashing in
between loses the attribution, which makes a later revert refuse to lift a ban it really
did cause — conservative, and recoverable by hand. The other order loses in the
dangerous direction: a `banned` row for a ban that was never issued has Timothy unban a
user it never touched, which is precisely what ADR 0005 forbids.

**Dry run records to the audit log, not to `enforcement_outcomes`.** CONTEXT.md says dry
run "records every enforcement it would perform", and phase 5 rehearses against
production data with it on. But an outcome row is an attribution claim, not a note —
writing `banned` for a ban that never happened would arm the revert path against
imaginary bans the moment dry run came off. So the intended action goes to the audit log,
where phase 5 can diff it, and the durable state stays empty.

**The breaker counts before it acts.** The limit is how many bans a guild may take in one
run, so the (limit + 1)-th trips it instead of landing. The bans already issued stay:
halting is what the rail is for, undoing is what `revert` is for.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from timothy_api import audit
from timothy_api.enforcement import outcomes, state
from timothy_api.enforcement.retry import with_backoff
from timothy_core.actors import Actor
from timothy_core.db.models import Guild, NotificationChannel
from timothy_core.enforcement.decisions import (
    Ban,
    Decision,
    Skip,
    SkipReason,
    Warn,
    decide,
    subscribed_listings,
)
from timothy_core.enforcement.messages import (
    BAN_COLOUR,
    ban_audit_reason,
    ban_notice,
    warn_notice,
)
from timothy_core.enums import OutcomeStatus
from timothy_core.ports.discord import DiscordError, Notice

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from timothy_api.enforcement.selfunbans import SelfUnbans
    from timothy_api.settings import Settings
    from timothy_core.enforcement.decisions import EnforcementRequest, PoolListing
    from timothy_core.ports.discord import DiscordPort

BREAKER_NOTICE = (
    "Timothy was about to take more than {limit} enforcement actions in this server in a "
    "single run, which is the safety limit. Nothing further has been enforced. Review the "
    "recent listings, then resume enforcement to continue."
)
"""What a guild is told when the breaker trips. It says what stopped and what to do,
because the guild's moderators are the ones who have to decide whether the burst was
legitimate. Red, like a ban: something a moderator has to deal with now."""

BREAKER_TITLE = "Enforcement paused here"

log = logging.getLogger(__name__)


def _outcome(decision: Decision | None) -> str:
    """The one word that says what happened to a pair, for the log's `decision` field.

    A `Skip` carries the reason it skipped, and that reason is the whole value of the
    line: `skip_not_listed` and `skip_user_absent` are the difference between a sweep
    with nothing to do and a sweep whose targets have all left.
    """
    match decision:
        case None:
            return "none"
        case Skip(reason=reason):
            return f"skip_{reason.value}"
        case _:
            return type(decision).__name__.lower()


@dataclass(slots=True)
class Run:
    """One job's worth of enforcement, and the breaker's memory of it.

    The threshold is per guild per run rather than per guild per hour, because the case
    it exists to catch — a bad migration, an accidental bulk listing — arrives as one
    fan-out. A guild that legitimately takes twenty-five bans an hour for hours is not
    what this is looking for.

    It counts *actions*, not bans. A warn-level subscription turns the same bad listing
    into a burst of notifications rather than a burst of bans, and a channel receiving
    three thousand messages is the same accident wearing a different hat. Counting only
    bans left the one guild in the migration data holding three pools at `warn` with no
    ceiling at all.
    """

    actions_by_guild: dict[int, int] = field(default_factory=dict)
    halted: set[int] = field(default_factory=set)

    def is_halted(self, guild_id: int) -> bool:
        """Whether the breaker has already stopped this run in this guild."""
        return guild_id in self.halted

    def take(self, guild_id: int, limit: int) -> bool:
        """Claim one action against this guild's budget for the run.

        `False` when the budget is spent, which is the caller's cue to trip the breaker.
        Claimed before the action rather than after, so the limit is how many may land
        and the one past it is stopped instead.
        """
        taken = self.actions_by_guild.get(guild_id, 0)
        if taken >= limit:
            return False
        self.actions_by_guild[guild_id] = taken + 1
        return True


class Enforcer:
    """Carries out decisions against Discord, and records what happened."""

    def __init__(
        self,
        *,
        discord: DiscordPort,
        settings: Settings,
        self_unbans: SelfUnbans,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Enforce through `discord`, under `settings`' rails."""
        self.discord = discord
        self.settings = settings
        self.self_unbans = self_unbans
        self.sleep = sleep

    async def enforce(
        self, session: AsyncSession, run: Run, *, guild_id: int, user_id: int
    ) -> Decision | None:
        """Decide about one user in one guild, and carry the answer out.

        `None` when there was nothing to decide: Timothy is not in the guild, or the
        breaker has already halted this run there.

        Every pair logs exactly one line at DEBUG, including the two that decide nothing.
        A fan-out is thousands of these, which is why it is not INFO — but it is the only
        record of *why* a user the operator expected to be banned was not, and the two
        `None` cases are the ones a sweep's own counters cannot tell apart.
        """
        if run.is_halted(guild_id):
            self._log_pair(guild_id, user_id, "halted")
            return None

        request = await state.gather(session, self.discord, guild_id=guild_id, user_id=user_id)
        if request is None:
            self._log_pair(guild_id, user_id, "not_in_guild")
            return None

        decision = decide(request)
        match decision:
            case Ban(justifications=justifications):
                await self._ban(session, run, request, justifications)
            case Warn(justifications=justifications):
                await self._warn(session, run, request, justifications)
            case Skip(reason=reason):
                await self._skip(session, request, reason)
        self._log_pair(guild_id, user_id, _outcome(decision))
        return decision

    def _log_pair(self, guild_id: int, user_id: int, decision: str) -> None:
        """One pair, one line.

        IDs go in `extra` as well as the message so the log store can filter on them
        without parsing a sentence (ADR 0015).
        """
        log.debug(
            "guild %d user %d: %s",
            guild_id,
            user_id,
            decision,
            extra={"guild_id": guild_id, "user_id": user_id, "decision": decision},
        )

    # -- the three answers ---------------------------------------------------

    async def _ban(
        self,
        session: AsyncSession,
        run: Run,
        request: EnforcementRequest,
        justifications: tuple[PoolListing, ...],
    ) -> None:
        guild_id = request.guild.guild_id
        if not run.take(guild_id, self.settings.enforcement_burst_limit):
            await self._trip_breaker(session, run, guild_id)
            return

        reason = ban_audit_reason(justifications)

        if self.settings.dry_run:
            self._audit_dry_run(session, request, action="ban", detail={"reason": reason})
            return

        try:
            await with_backoff(
                lambda: self.discord.ban(
                    guild_id=guild_id, user_id=request.user_id, reason=reason
                ),
                sleep=self.sleep,
            )
        except DiscordError as error:
            await self._record_failure(session, request, justifications, error)
            return

        for listing in justifications:
            await outcomes.record(
                session,
                guild_id=guild_id,
                user_id=request.user_id,
                pool_id=listing.pool_id,
                status=OutcomeStatus.BANNED,
                reason=listing.reason,
            )
        audit.record(
            session,
            actor=Actor.system(),
            action=audit.AuditAction.ENFORCEMENT_BAN,
            target=audit.guild_user_target(guild_id=guild_id, user_id=request.user_id),
            detail={
                "pool_ids": [listing.pool_id for listing in justifications],
                "reason": reason,
            },
        )
        # After the record, not before: the attribution is what makes the ban revertible,
        # and a channel that has been deleted must not cost us that.
        await self._notify(
            session,
            guild_id,
            ban_notice(user_id=request.user_id, justifications=justifications),
        )

    async def _warn(
        self,
        session: AsyncSession,
        run: Run,
        request: EnforcementRequest,
        justifications: tuple[PoolListing, ...],
    ) -> None:
        guild_id = request.guild.guild_id
        channel = await session.get(NotificationChannel, guild_id)

        if channel is None:
            await self._warn_without_a_channel(session, request, justifications)
            return

        for listing in justifications:
            if not run.take(guild_id, self.settings.enforcement_burst_limit):
                await self._trip_breaker(session, run, guild_id)
                return

            notice = warn_notice(user_id=request.user_id, listing=listing)
            if self.settings.dry_run:
                self._audit_dry_run(
                    session,
                    request,
                    action="warn",
                    detail={"pool_id": listing.pool_id, "channel_id": str(channel.channel_id)},
                )
                continue
            try:
                await with_backoff(
                    lambda notice=notice: self.discord.post_message(
                        channel_id=channel.channel_id, notice=notice
                    ),
                    sleep=self.sleep,
                )
            except DiscordError as error:
                await self._record_failure(session, request, (listing,), error)
                continue

            await outcomes.record(
                session,
                guild_id=guild_id,
                user_id=request.user_id,
                pool_id=listing.pool_id,
                status=OutcomeStatus.WARNED,
                reason=listing.reason,
            )
            audit.record(
                session,
                actor=Actor.system(),
                action=audit.AuditAction.ENFORCEMENT_WARN,
                target=audit.guild_user_target(guild_id=guild_id, user_id=request.user_id),
                detail={"pool_id": listing.pool_id, "channel_id": str(channel.channel_id)},
            )

    async def _warn_without_a_channel(
        self,
        session: AsyncSession,
        request: EnforcementRequest,
        justifications: tuple[PoolListing, ...],
    ) -> None:
        """A warn-level match with nowhere to report it.

        Recorded as `failed` rather than `warned`, so that setting a channel later lets
        the sweep deliver what was missed. A `warned` row here would silently consume the
        one warning this user was ever going to get.
        """
        if self.settings.dry_run:
            self._audit_dry_run(
                session, request, action="warn", detail={"blocked": "no notification channel"}
            )
            return

        for listing in justifications:
            await outcomes.record(
                session,
                guild_id=request.guild.guild_id,
                user_id=request.user_id,
                pool_id=listing.pool_id,
                status=OutcomeStatus.FAILED,
                reason="no notification channel is set for this guild",
            )
        audit.record(
            session,
            actor=Actor.system(),
            action=audit.AuditAction.ENFORCEMENT_FAILED,
            target=audit.guild_user_target(
                guild_id=request.guild.guild_id, user_id=request.user_id
            ),
            detail={"error": "no notification channel is set for this guild"},
        )

    async def _skip(
        self, session: AsyncSession, request: EnforcementRequest, reason: SkipReason
    ) -> None:
        """Record the one skip a moderator will later ask about, and no others.

        `skipped_exception` is durable state: it is what stops the sweep asking Discord
        about this user every hour. The other skips deliberately record nothing —
        recording `user_absent` would leave the door disarmed for a user who joins
        tomorrow, and recording `enforcement_paused` would survive the resume.
        """
        if reason is not SkipReason.EXCEPTION:
            return

        for listing in subscribed_listings(request):
            await outcomes.record(
                session,
                guild_id=request.guild.guild_id,
                user_id=request.user_id,
                pool_id=listing.pool_id,
                status=OutcomeStatus.SKIPPED_EXCEPTION,
                reason=listing.reason,
            )

    # -- the rails -----------------------------------------------------------

    async def _trip_breaker(self, session: AsyncSession, run: Run, guild_id: int) -> None:
        """Halt this run in this guild, and ask for a human.

        In dry run the halt is simulated but the pause is not persisted: a rehearsal
        against production data (PLAN.md, phase 5) must not leave real guilds paused when
        dry run comes off. The audit row says which it was.
        """
        run.halted.add(guild_id)
        limit = self.settings.enforcement_burst_limit
        # A rail firing is the loudest thing that happens here and it had no log line at
        # all: the audit row is durable but nobody is watching that table at 3am.
        log.warning(
            "circuit breaker tripped in guild %d after %d action(s)%s",
            guild_id,
            limit,
            " (dry run)" if self.settings.dry_run else "",
            extra={
                "guild_id": guild_id,
                "burst_limit": limit,
                "dry_run": self.settings.dry_run,
            },
        )

        if not self.settings.dry_run:
            guild = await session.get(Guild, guild_id)
            if guild is not None:
                guild.enforcement_paused = True

        audit.record(
            session,
            actor=Actor.system(),
            action=audit.AuditAction.ENFORCEMENT_BREAKER_TRIPPED,
            target=audit.guild_target(guild_id),
            detail={"burst_limit": limit, "dry_run": self.settings.dry_run},
        )
        if not self.settings.dry_run:
            await self._notify(
                session,
                guild_id,
                Notice(
                    title=BREAKER_TITLE,
                    body=BREAKER_NOTICE.format(limit=limit),
                    colour=BAN_COLOUR,
                ),
            )

    async def _notify(self, session: AsyncSession, guild_id: int, notice: Notice) -> None:
        """Tell a guild something, if it has said where. Never fails the caller."""
        channel = await session.get(NotificationChannel, guild_id)
        if channel is None:
            return
        # The notice is a courtesy; the action it reports is the point. A guild that has
        # deleted its notification channel still gets paused, and still gets its bans.
        with suppress(DiscordError):
            await with_backoff(
                lambda: self.discord.post_message(channel_id=channel.channel_id, notice=notice),
                sleep=self.sleep,
            )

    # -- recording -----------------------------------------------------------

    async def _record_failure(
        self,
        session: AsyncSession,
        request: EnforcementRequest,
        justifications: tuple[PoolListing, ...],
        error: DiscordError,
    ) -> None:
        """A Discord call that retrying did not fix.

        Not a job failure. The everyday cause is a guild that granted Timothy no ban
        permission, or a listed user who outranks it, and neither is fixed by running the
        same job again. The `failed` outcome is what the sweep looks for, so the retry
        happens when the world might have changed.
        """
        for listing in justifications:
            await outcomes.record(
                session,
                guild_id=request.guild.guild_id,
                user_id=request.user_id,
                pool_id=listing.pool_id,
                status=OutcomeStatus.FAILED,
                reason=str(error),
            )
        audit.record(
            session,
            actor=Actor.system(),
            action=audit.AuditAction.ENFORCEMENT_FAILED,
            target=audit.guild_user_target(
                guild_id=request.guild.guild_id, user_id=request.user_id
            ),
            detail={
                "pool_ids": [listing.pool_id for listing in justifications],
                "error": str(error),
            },
        )

    def _audit_dry_run(
        self,
        session: AsyncSession,
        request: EnforcementRequest,
        *,
        action: str,
        detail: dict[str, object],
    ) -> None:
        audit.record(
            session,
            actor=Actor.system(),
            action=audit.AuditAction.ENFORCEMENT_DRY_RUN,
            target=audit.guild_user_target(
                guild_id=request.guild.guild_id, user_id=request.user_id
            ),
            detail={"would": action, **detail},
        )
