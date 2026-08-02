"""FastAPI wiring: process singletons, a session per request, and the authorization gate.

:mod:`timothy_api.policy` decides; this carries the decision out. Splitting them is what
keeps ADR 0001's "one policy module" honest — a handler names an
:class:`~timothy_api.policy.Operation` and never mentions `ADMINISTRATOR`, a guild, or a
status code.

Nothing here defers its annotations. FastAPI evaluates a dependency's signature at import
time to build the injection graph and the OpenAPI schema, so the types it reads have to
exist at runtime.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timothy_api import policy
from timothy_api.db import Database
from timothy_api.identity import CallerActor
from timothy_api.permissions import PermissionResolver
from timothy_api.policy import Operation, PermissionContext, Requirement
from timothy_api.settings import Settings
from timothy_core.actors import Actor
from timothy_core.db.models import Guild


def get_settings(request: Request) -> Settings:
    """The process settings, fixed at startup."""
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    """The one engine this process has."""
    database: Database = request.app.state.db
    return database


def get_resolver(request: Request) -> PermissionResolver:
    """The permission resolver, with the process-wide cache behind it."""
    resolver: PermissionResolver = request.app.state.resolver
    return resolver


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    """A session for the duration of one request.

    Handlers commit for themselves — FastAPI closes a `yield` dependency only after the
    response has been sent, so a commit here would be able to fail with the client
    already holding a 200.
    """
    async with database.sessions() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ResolverDep = Annotated[PermissionResolver, Depends(get_resolver)]


FROM_GUILD_HEADER: Final = "X-Timothy-From-Guild"
"""Where the caller's interaction came from. Ordering only — see :func:`_scan_order`."""


async def _timothys_guild_ids(session: AsyncSession) -> list[int]:
    result = await session.scalars(select(Guild.guild_id))
    return list(result)


def _scan_order(guild_ids: list[int], request: Request) -> list[int]:
    """The guilds to check for membership, most likely first.

    `ANY_GUILD_MEMBER` is answered by asking Discord "is this person here?" once per guild
    until one says yes. Discord paces that at roughly two calls a second, so across a
    hundred-odd guilds an unlucky order is most of a minute — well past the 2.5 seconds
    the bot waits and the three Discord allows an interaction. `/list_pools` is the one
    command that needs this permission and the one command a member with no administrator
    anywhere can reach, so in practice it was the users with the least power who got the
    timeout.

    A caller that names the guild it is calling from gets that guild checked first. This
    is a hint and grants nothing: the answer still comes from Discord, and every other
    guild is still scanned behind it, so a header naming a guild the caller is not in
    costs one wasted call and changes no decision (ADR 0001).
    """
    named = request.headers.get(FROM_GUILD_HEADER, "")
    if not named.isdigit():
        return guild_ids
    first = int(named)
    if first not in guild_ids:
        return guild_ids
    return [first, *(guild_id for guild_id in guild_ids if guild_id != first)]


def _target_guild_id(request: Request) -> int:
    """The guild a request is about, taken from its path.

    Every operation requiring authority over a guild is routed under
    `/guilds/{guild_id}`. A route that asks for one of those without that path parameter
    is a wiring mistake, and this is where it surfaces — loudly, at the first request,
    rather than as a check that quietly passes.
    """
    raw = request.path_params.get("guild_id")
    if raw is None:  # pragma: no cover — a routing bug, not a reachable request
        msg = f"{request.url.path} needs a guild_id path parameter to authorize against"
        raise RuntimeError(msg)
    return int(raw)


class Requires:
    """Dependency that admits a caller to one operation, or refuses with 403.

    Resolves exactly the one fact :func:`timothy_api.policy.requirement` names, so a pool
    operation costs a single permission lookup and never a scan of every guild.
    """

    def __init__(self, operation: Operation) -> None:
        """Guard `operation`."""
        self.operation = operation

    async def __call__(
        self,
        request: Request,
        actor: CallerActor,
        settings: SettingsDep,
        session: SessionDep,
        resolver: ResolverDep,
    ) -> Actor:
        """Resolve, decide, and hand the actor on to the handler."""
        context = await self._resolve(request, actor, settings, session, resolver)
        if not policy.allows(self.operation, context):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"not permitted: {self.operation.value}",
            )
        return actor

    async def _resolve(
        self,
        request: Request,
        actor: Actor,
        settings: Settings,
        session: AsyncSession,
        resolver: ResolverDep,
    ) -> PermissionContext:
        user_id = actor.user_id
        needed = policy.requirement(self.operation)
        if user_id is None:
            return PermissionContext(actor=actor)

        if needed is Requirement.MANAGEMENT_ADMIN:
            return PermissionContext(
                actor=actor,
                management_admin=await resolver.is_administrator(
                    guild_id=settings.management_guild_id, user_id=user_id
                ),
            )
        if needed is Requirement.TARGET_GUILD_ADMIN:
            return PermissionContext(
                actor=actor,
                target_guild_admin=await resolver.is_administrator(
                    guild_id=_target_guild_id(request), user_id=user_id
                ),
            )
        if needed is Requirement.ANY_GUILD_MEMBER:
            return PermissionContext(
                actor=actor,
                any_guild_member=await resolver.is_member_of_any(
                    guild_ids=_scan_order(await _timothys_guild_ids(session), request),
                    user_id=user_id,
                ),
            )
        return PermissionContext(actor=actor)
