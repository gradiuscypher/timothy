"""Why Timothy cannot ban here, told to the people who can fix it.

Three questions a guild administrator has no other way to answer:

* **Has this guild granted Timothy the ban permission at all?** Today the only evidence
  is `failed` outcomes piling up, and `GET /ops/failures` — which belongs to whoever runs
  the deployment, not to the administrator who can grant the permission in thirty seconds.
* **Which roles are out of reach?** Discord's own role list shows Timothy's position
  beside everyone else's and gives no hint that *level with* means unbannable.
* **Why did this particular ban fail?** The stored outcome carries whatever discord.py
  said, which is usually `403 Forbidden` and never says whose role was in the way.

The first two are answered from the bot's snapshot, and cost no Discord call: the numbers
are already in the database because the gateway had them for free (ADR 0016). The third
is answered *live*, one member lookup, because somebody reading it is about to go and
move a role and wants to know whether it would work today — not what was true when the
ban failed last week.

The routes are not all under one prefix. `/diagnostics/pending` is the bot collecting
work, and there is no guild in its path to authorize against; everything else is one
guild's own business and lives under `/guilds/{guild_id}` so that
`Requirement.TARGET_GUILD_ADMIN` has something to resolve.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy import select

from timothy_api import diagnostics
from timothy_api.deps import DiscordDep, Requires, SessionDep, SettingsDep
from timothy_api.lookups import get_guild, not_found
from timothy_api.policy import Operation
from timothy_api.schemas import (
    BanFailureDiagnosis,
    BanFailureRead,
    DiagnosticsRefreshAck,
    DiagnosticsReport,
    GuildDiagnosticsRead,
    PendingDiagnostics,
    RoleRead,
    Snowflake,
)
from timothy_core.actors import Actor
from timothy_core.db.models import EnforcementOutcome, GuildDiagnostics, GuildRole, Pool
from timothy_core.enforcement import diagnosis
from timothy_core.enums import OutcomeStatus
from timothy_core.ports.discord import DiscordError

log = logging.getLogger(__name__)

router = APIRouter(tags=["diagnostics"])

Reader = Annotated[Actor, Depends(Requires(Operation.READ_GUILD_DIAGNOSTICS))]
Reporter = Annotated[Actor, Depends(Requires(Operation.REPORT_GUILD_DIAGNOSTICS))]

GuildId = Annotated[Snowflake, Path(description="A Discord guild ID.")]
UserId = Annotated[Snowflake, Path(description="A Discord user ID.")]
Limit = Annotated[int, Query(ge=1, le=200, description="How many failures to return.")]


def get_refresh_queue(request: Request) -> diagnostics.RefreshQueue:
    """The guilds waiting to be re-checked by the bot."""
    queue: diagnostics.RefreshQueue = request.app.state.refresh_queue
    return queue


RefreshQueueDep = Annotated[diagnostics.RefreshQueue, Depends(get_refresh_queue)]

NEVER_OBSERVED = "diagnostics: the bot has not reported on this guild yet"
"""Said rather than defaulted. A guild whose bot has never connected is not a guild where
everything is fine, and the two must not render the same."""


# -- what an administrator sees ---------------------------------------------------------


@router.get("/guilds/{guild_id}/diagnostics")
async def read_diagnostics(
    guild_id: GuildId,
    _actor: Reader,
    session: SessionDep,
    settings: SettingsDep,
) -> GuildDiagnosticsRead:
    """Whether Timothy can ban in this guild, and which roles it can never reach.

    Costs no Discord call at all. Everything here was observed by the bot and stored.

    Raises:
        HTTPException: 404 if Timothy is not in the guild, or has never looked at it.
    """
    await get_guild(session, guild_id)

    snapshot = await diagnostics.read(session, guild_id)
    if snapshot is None:
        raise not_found(NEVER_OBSERVED)
    stored, roles = snapshot

    return _render(
        stored,
        roles,
        stale=diagnostics.is_stale(
            stored, settings.diagnostics_interval, now=datetime.now(UTC)
        ),
    )


@router.post("/guilds/{guild_id}/diagnostics/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh_diagnostics(
    guild_id: GuildId,
    _actor: Reader,
    session: SessionDep,
    queue: RefreshQueueDep,
) -> DiagnosticsRefreshAck:
    """Ask for this guild to be looked at again, out of turn.

    202 and not 200: the backend cannot reach the bot, so all this does is record the
    request. The bot collects it on its next poll and the snapshot changes underneath the
    caller, which is why the UI polls rather than reading this response for an answer.
    """
    await get_guild(session, guild_id)
    queue.request(guild_id)
    return DiagnosticsRefreshAck.model_validate({"guild_id": guild_id, "requested": True})


@router.get("/guilds/{guild_id}/diagnostics/failures")
async def list_ban_failures(
    guild_id: GuildId,
    _actor: Reader,
    session: SessionDep,
    limit: Limit = 50,
) -> list[BanFailureRead]:
    """Every ban Timothy tried to issue here and could not, newest first.

    Database only, deliberately — no Discord call, so the list appears at once however
    long it is. The explanation of any one row is a separate request, made when somebody
    actually asks for it.

    The join to `pools` is an outer one because `enforcement_outcomes` holds no foreign
    keys (ADR 0005): a failure survives the pool that caused it being deleted, and losing
    those rows would quietly shorten the list.
    """
    await get_guild(session, guild_id)

    rows = await session.execute(
        select(EnforcementOutcome, Pool.name)
        .outerjoin(Pool, Pool.id == EnforcementOutcome.pool_id)
        .where(
            EnforcementOutcome.guild_id == guild_id,
            EnforcementOutcome.status == OutcomeStatus.FAILED,
        )
        .order_by(EnforcementOutcome.attempted_at.desc())
        .limit(limit)
    )
    return [
        BanFailureRead.model_validate(
            {
                "user_id": outcome.user_id,
                "pool_id": outcome.pool_id,
                "pool_name": pool_name,
                "reason": outcome.reason,
                "attempted_at": outcome.attempted_at,
            }
        )
        for outcome, pool_name in rows
    ]


@router.get("/guilds/{guild_id}/diagnostics/failures/{user_id}")
async def diagnose_ban_failure(
    guild_id: GuildId,
    user_id: UserId,
    _actor: Reader,
    session: SessionDep,
    discord: DiscordDep,
) -> BanFailureDiagnosis:
    """Why Timothy could not ban this user here, as things stand now.

    One `fetch_member` — the port's existing operation, because what the domain needs is
    the roles this person holds and nothing wider (ADR 0007 stands unamended). Their
    positions come from the stored snapshot.

    Discord failing here does not fail the request. Somebody is reading this *because*
    something is wrong, and answering 502 would replace a partial explanation with none;
    the verdict degrades to `unknown` and Discord's own words from the failed outcome are
    still shown.

    Raises:
        HTTPException: 404 if Timothy is not in the guild, or has never looked at it.
    """
    await get_guild(session, guild_id)

    snapshot = await diagnostics.read(session, guild_id)
    if snapshot is None:
        raise not_found(NEVER_OBSERVED)
    stored, roles = snapshot

    outcome = await session.scalar(
        select(EnforcementOutcome).where(
            EnforcementOutcome.guild_id == guild_id,
            EnforcementOutcome.user_id == user_id,
            EnforcementOutcome.status == OutcomeStatus.FAILED,
        )
    )

    role_ids, reachable = await _role_ids(discord, guild_id=guild_id, user_id=user_id)
    verdict = diagnosis.diagnose(
        standing=diagnostics.standing_of(stored, roles),
        user_id=user_id,
        role_ids=role_ids,
        lookup_failed=not reachable,
        detail=outcome.reason if outcome is not None else None,
    )
    return BanFailureDiagnosis(
        user_id=user_id,
        blocker=verdict.blocker,
        blocking_roles=[_role(role) for role in verdict.blocking_roles],
        timothy_top_role_position=stored.top_role_position,
        timothy_top_role_name=stored.top_role_name,
        detail=verdict.detail,
    )


# -- what the bot reports ---------------------------------------------------------------


@router.put("/guilds/{guild_id}/diagnostics")
async def report_diagnostics(
    guild_id: GuildId,
    body: DiagnosticsReport,
    _actor: Reporter,
    session: SessionDep,
) -> GuildDiagnosticsRead:
    """Record what the gateway sees of this guild.

    Idempotent and wholesale: the roles are replaced rather than merged, so one deleted in
    Discord stops being reported here.
    """
    await get_guild(session, guild_id)

    await diagnostics.record(session, guild_id=guild_id, report=body)
    await session.commit()

    # Read back rather than render the report: what the bot sent and what the database
    # now holds should be the same thing, and answering from the stored rows is what
    # makes that true rather than assumed.
    stored, roles = await diagnostics.read(session, guild_id) or (None, [])
    if stored is None:  # pragma: no cover — just written in this transaction
        raise not_found(NEVER_OBSERVED)
    return _render(stored, roles, stale=False)


@router.get("/diagnostics/pending")
async def pending_diagnostics(_actor: Reporter, queue: RefreshQueueDep) -> PendingDiagnostics:
    """The guilds an administrator has asked to have looked at again.

    Reading drains: see :meth:`~timothy_api.diagnostics.RefreshQueue.drain`. Not scoped to
    a guild, because the caller is the bot asking what work there is rather than anybody
    asking about somewhere in particular.
    """
    return PendingDiagnostics.model_validate({"guild_ids": queue.drain()})


# -- internals --------------------------------------------------------------------------


def _role(role: diagnosis.Role) -> RoleRead:
    """The domain's role, on the wire."""
    return RoleRead(
        role_id=role.role_id,
        name=role.name,
        position=role.position,
        member_count=role.member_count,
        managed=role.managed,
    )


def _render(
    stored: GuildDiagnostics, roles: list[GuildRole], *, stale: bool
) -> GuildDiagnosticsRead:
    """One stored snapshot, with the hierarchy question already answered.

    The filtering happens here rather than in the browser on purpose: "at or above" is
    Discord's strict-inequality rule, and it is the exact thing a reader of the role list
    gets wrong. Sending every role and a number to compare would ship that mistake to the
    client.
    """
    unbannable = diagnosis.unbannable_roles(diagnostics.standing_of(stored, roles))
    return GuildDiagnosticsRead(
        guild_id=stored.guild_id,
        observed_at=stored.observed_at,
        stale=stale,
        can_ban=stored.can_ban,
        is_administrator=stored.is_administrator,
        top_role_position=stored.top_role_position,
        top_role_name=stored.top_role_name,
        member_counts_complete=stored.member_counts_complete,
        unbannable_roles=[_role(role) for role in unbannable],
        unbannable_members=diagnostics.unbannable_members(unbannable),
    )


async def _role_ids(
    discord: DiscordDep, *, guild_id: int, user_id: int
) -> tuple[frozenset[int] | None, bool]:
    """The roles this user holds right now, and whether Discord answered at all.

    The second half is not decoration. "Not in the guild" and "we could not ask" both
    produce no roles, and collapsing them would report a user who has left — telling an
    administrator their problem had solved itself, at the exact moment Timothy had lost
    the ability to see it.
    """
    try:
        member = await discord.fetch_member(guild_id=guild_id, user_id=user_id)
    except DiscordError:
        log.warning("could not resolve %s in %s for diagnosis", user_id, guild_id)
        return None, False
    return (member.role_ids if member is not None else None), True
