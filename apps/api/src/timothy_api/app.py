"""FastAPI application factory.

Everything the process needs is built once in the lifespan and hung on `app.state`: the
engine, the Discord port, and the permission cache in front of it. The port is injectable
because the tests drive the whole API against
:class:`~timothy_core.ports.fake.FakeDiscord` — ADR 0007's point is that authorization
and enforcement are testable at full speed with no network, and that only holds if the
application object can be handed a different Discord.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from timothy_api import __version__, errors, routers
from timothy_api.db import Database
from timothy_api.discord_adapter import DiscordAdapter
from timothy_api.identity import require_service_token
from timothy_api.permissions import PermissionResolver
from timothy_api.settings import Settings
from timothy_core.ports.discord import DiscordPort

DESCRIPTION = """\
Shared moderation service for Discord.

Callers assert identity and never authority: present the internal token as a bearer
credential, and name the acting Discord user in `X-Timothy-Actor`. What that user may do
is resolved against Discord itself.

Snowflakes — guild, user and channel IDs — are strings on the wire. They are 64-bit, and
JavaScript numbers are not.
"""


class Health(BaseModel):
    """Liveness payload, used by the compose healthcheck."""

    status: Literal["ok"]
    version: str


def create_app(
    settings: Settings | None = None,
    *,
    discord_port: DiscordPort | None = None,
) -> FastAPI:
    """Build the application.

    Args:
        settings: process configuration. Read from the environment when omitted.
        discord_port: the door to Discord. A real, lazily logged-in adapter when omitted.
    """
    resolved = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Migrate, wire, serve, and let go.

        Migrations run here rather than in an entrypoint script so that a container
        coming up on an empty volume brings its own schema — the revisions travel inside
        the wheel.
        """
        database = Database(resolved.database_url)
        await database.migrate()

        port = discord_port
        if port is None:
            port = DiscordAdapter.create(resolved.discord_token.get_secret_value())

        app.state.settings = resolved
        app.state.db = database
        app.state.discord = port
        app.state.resolver = PermissionResolver(port, ttl=resolved.permission_cache_ttl)

        try:
            yield
        finally:
            await database.dispose()
            if isinstance(port, DiscordAdapter):
                await port.close()

    app = FastAPI(
        title="Timothy",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    errors.install(app)

    @app.get("/health", tags=["health"])
    async def health() -> Health:
        """Outside the token, because the compose healthcheck has no credentials."""
        return Health(status="ok", version=__version__)

    app.include_router(routers.api, dependencies=[Depends(require_service_token)])
    return app


app = create_app()
