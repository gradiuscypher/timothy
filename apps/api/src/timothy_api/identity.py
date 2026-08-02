"""Who is calling, and on whose behalf.

Two separate questions, and conflating them is what ADR 0003 warns against. There are now
two kinds of caller, and they answer the pair differently:

* **A service** — the bot container, the migration tool — presents the internal token as
  a bearer credential and names the actor it is speaking for in `X-Timothy-Actor`. The
  token says which *process*; the header says which *person*. The header carries no
  authority of its own: what that person may do is resolved against Discord afterwards.
* **A browser** presents a session cookie, which is both answers at once. The session row
  names the actor, so there is nothing for the caller to assert — and therefore nothing
  to forge. An `X-Timothy-Actor` sent alongside a session is refused rather than ignored,
  because a client that sends one is confused about which of these it is.

The token matters because nginx proxies `/api` from the public tunnel. Without it, an
identity assertion would be an authority assertion, and anyone could send an
administrator's user ID (ADR 0008).

Annotations are not deferred here: FastAPI reads a dependency's signature at import time
to build the injection graph, so every type it sees has to exist at runtime.
"""

import secrets
from dataclasses import dataclass
from typing import Annotated, Final
from urllib.parse import urlsplit

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from timothy_api import sessions
from timothy_api.db import Database
from timothy_api.settings import Settings
from timothy_core.actors import Actor

ACTOR_HEADER: Final = "X-Timothy-Actor"

SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
"""Methods that change nothing, and so need no cross-site protection."""

_bearer = HTTPBearer(
    description="The shared internal token. Never leaves the compose network.",
    auto_error=False,
)
_actor_header = APIKeyHeader(
    name=ACTOR_HEADER,
    description=(
        "Who the call is on behalf of: `user:<snowflake>`, or `system` for Timothy "
        "itself. Service callers only — a browser's session names its own actor."
    ),
    auto_error=False,
)
"""Both refuse to raise for themselves. FastAPI's own errors here disagree with each
other — a missing bearer is a 403 and a missing API key a 401 — and the statuses matter
more than the convenience: a caller that sent no credential has not authenticated (401),
and a caller that authenticated but garbled its actor sent a bad request (400)."""


@dataclass(frozen=True, slots=True)
class Caller:
    """An authenticated caller, and what is known about them from authenticating.

    Attributes:
        actor: whom this call is for.
        guild_ids: for a browser, Discord's answer at login to which guilds this person
            is in. `None` for a service caller, which means "no snapshot" rather than
            "no guilds" — the difference decides whether a membership check may be
            narrowed (ADR 0010).
        session_token: the cookie this caller presented, so logging out can revoke it.
    """

    actor: Actor
    guild_ids: frozenset[int] | None = None
    session_token: str | None = None


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _database(request: Request) -> Database:
    """The engine, reached without :mod:`timothy_api.deps`.

    Authentication runs before the request's own session exists — and before the policy
    layer that `deps` is built around — so it opens its own. Importing `deps` here would
    be a cycle: `deps` needs the actor this module resolves.
    """
    database: Database = request.app.state.db
    return database


def _parse_actor(raw: str | None) -> Actor:
    """Read an `X-Timothy-Actor`, or say why it is not one.

    Raises:
        HTTPException: 400 if the header is missing or is not an actor.
    """
    try:
        return Actor.parse(raw or "")
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"malformed {ACTOR_HEADER}: expected 'user:<snowflake>' or 'system'",
        ) from error


def _check_same_origin(request: Request) -> None:
    """Refuse a browser's state change that came from somebody else's page.

    `SameSite=Lax` already means the cookie is not sent on a cross-site POST, so this is
    the second of two locks rather than the only one. It is here because the first one is
    a browser default that a future embedding, a redirect chain or a `SameSite=None`
    someone adds for an unrelated reason could quietly turn off, and the failure would be
    silent and total.

    Every browser sends `Origin` on a request that is not a GET, so a missing one is not
    a browser making an ordinary request.

    Raises:
        HTTPException: 403 if the origin is absent or is not this host.
    """
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host or urlsplit(origin).netloc != host:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cross-origin request refused",
        )


async def _from_session(request: Request, token: str) -> Caller | None:
    """The caller a session cookie names, or `None` if the cookie is stale."""
    database = _database(request)
    async with database.sessions() as session:
        signed_in = await sessions.lookup(session, token)
    if signed_in is None:
        return None
    return Caller(
        actor=signed_in.actor,
        guild_ids=signed_in.guild_ids,
        session_token=token,
    )


async def authenticate(
    request: Request,
    settings: Annotated[Settings, Depends(_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    raw_actor: Annotated[str | None, Depends(_actor_header)],
    session_cookie: Annotated[
        str | None, Cookie(alias=sessions.COOKIE_NAME, description="A browser session.")
    ] = None,
) -> Caller:
    """Establish who is calling, by whichever of the two credentials they brought.

    A bearer credential that is present must be correct: a wrong token alongside a valid
    cookie is a client in trouble, not a browser, and quietly falling back would hide it.

    An unconfigured internal token rejects every service caller rather than accepting
    them. The alternative — treating "no token set" as "no token needed" — would turn a
    missing environment variable into an open API, which is the failure mode this exists
    to prevent.

    Raises:
        HTTPException: 401 with no usable credential, 400 for a malformed actor header,
            403 for a browser's cross-origin state change.
    """
    if credentials is not None:
        expected = settings.internal_token.get_secret_value()
        if not expected or not secrets.compare_digest(credentials.credentials, expected):
            raise _unauthenticated("invalid internal token")
        return Caller(actor=_parse_actor(raw_actor))

    if session_cookie:
        caller = await _from_session(request, session_cookie)
        if caller is None:
            raise _unauthenticated("session expired")
        if raw_actor is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"a browser session may not send {ACTOR_HEADER}",
            )
        _check_same_origin(request)
        return caller

    raise _unauthenticated("no credentials")


def _unauthenticated(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


CallerDep = Annotated[Caller, Depends(authenticate)]
"""The one way in. FastAPI caches a dependency per request, so the router-level gate,
:class:`~timothy_api.deps.Requires` and a handler asking for the caller directly all
share a single authentication — and a single session lookup."""
