"""Who is calling, and on whose behalf.

Two separate questions, and conflating them is what ADR 0003 warns against. The bearer
token authenticates the *caller* — the bot container, or in phase 6 the session layer in
front of the browser. The `X-Timothy-Actor` header names the *actor* the caller is
speaking for, and carries no authority of its own: what that actor may do is resolved
against Discord afterwards.

The token matters because nginx proxies `/api` from the public tunnel. Without it, an
identity assertion would be an authority assertion, and anyone could send an
administrator's user ID.

Annotations are not deferred here: FastAPI reads a dependency's signature at import time
to build the injection graph, so every type it sees has to exist at runtime.
"""

import secrets
from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from timothy_api.settings import Settings
from timothy_core.actors import Actor

ACTOR_HEADER: Final = "X-Timothy-Actor"

_bearer = HTTPBearer(
    description="The shared internal token. Never leaves the compose network.",
    auto_error=False,
)
_actor_header = APIKeyHeader(
    name=ACTOR_HEADER,
    description=(
        "Who the call is on behalf of: `user:<snowflake>`, or `system` for Timothy itself."
    ),
    auto_error=False,
)
"""Both refuse to raise for themselves. FastAPI's own errors here disagree with each
other — a missing bearer is a 403 and a missing API key a 401 — and the statuses matter
more than the convenience: a caller that sent no token has not authenticated (401), and
a caller that authenticated but garbled its actor sent a bad request (400)."""


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def require_service_token(
    settings: Annotated[Settings, Depends(_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Reject anything that does not hold the internal token.

    An unconfigured token rejects everything. The alternative — treating "no token set"
    as "no token needed" — would turn a missing environment variable into an open API,
    which is the failure mode this exists to prevent.

    Raises:
        HTTPException: 401 if the token is absent, wrong, or unconfigured.
    """
    expected = settings.internal_token.get_secret_value()
    presented = credentials.credentials if credentials is not None else ""
    if not expected or not secrets.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def caller_actor(raw: Annotated[str | None, Depends(_actor_header)]) -> Actor:
    """The actor the caller is speaking for.

    Always explicit. Letting a missing header mean `system` would turn a client bug into
    a claim to be Timothy, and Timothy's own operations are the ones with no Discord
    permission behind them to check.

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


ServiceToken = Annotated[None, Depends(require_service_token)]
CallerActor = Annotated[Actor, Depends(caller_actor)]
