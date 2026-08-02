"""Turning the failures below the API into the statuses above it.

Two families reach here. Discord's failures are not the caller's fault, so they map to
5xx and 429 rather than to 400 — a moderator whose `/add_ban` fails because Discord is
down should be told to try again, not told they did something wrong. Constraint
violations map to 409: handlers check for the obvious collisions first and give a better
message, and this catches the ones that slip between a check and a commit.
"""

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from timothy_core.ports.discord import (
    DiscordError,
    DiscordUnavailableError,
    RateLimitedError,
)


async def discord_error_handler(_request: Request, exc: Exception) -> Response:
    """Report a Discord failure as something the caller can act on."""
    if isinstance(exc, RateLimitedError):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Discord rate limited Timothy"},
            headers={"Retry-After": str(int(exc.retry_after) + 1)},
        )
    if isinstance(exc, DiscordUnavailableError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Discord is unreachable"},
        )
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"Discord refused the request: {exc}"},
    )


async def integrity_error_handler(_request: Request, _exc: Exception) -> Response:
    """A uniqueness or foreign-key constraint said no."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "that already exists, or something it refers to does not"},
    )


def install(app: FastAPI) -> None:
    """Attach the handlers to an application."""
    app.add_exception_handler(DiscordError, discord_error_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
