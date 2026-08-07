"""FastAPI application factory.

Everything the process needs is built once in the lifespan and hung on `app.state`: the
engine, the Discord port, the permission cache in front of it, and the enforcement
machinery. The port is injectable because the tests drive the whole API against
:class:`~timothy_core.ports.fake.FakeDiscord` — ADR 0007's point is that authorization
and enforcement are testable at full speed with no network, and that only holds if the
application object can be handed a different Discord.

The worker and the sweep scheduler run here, in the API's own process and on its event
loop (ADR 0003). They are background tasks of the lifespan, so they start after the
migrations and are cancelled before the engine is disposed. `TIMOTHY_WORKERS_ENABLED`
turns them off, which is how most tests get a queue that only moves when they say so.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from timothy_api import __version__, errors, routers
from timothy_api.db import Database
from timothy_api.diagnostics import RefreshQueue
from timothy_api.discord_adapter import DiscordAdapter
from timothy_api.enforcement import Enforcer, JobContext, SelfUnbans, Sweeper, Worker
from timothy_api.identity import authenticate
from timothy_api.oauth import DiscordOAuth, OAuthPort
from timothy_api.permissions import PermissionResolver
from timothy_api.routers import auth
from timothy_api.settings import Settings
from timothy_core.ports.discord import DiscordPort

log = logging.getLogger(__name__)

DESCRIPTION = """\
Shared moderation service for Discord.

Callers assert identity and never authority. There are two ways to do it:

* **Services** present the internal token as a bearer credential and name the acting
  Discord user in `X-Timothy-Actor`.
* **Browsers** present the session cookie `/api/auth/login` issues, which names the actor
  itself. Sending `X-Timothy-Actor` alongside one is an error.

Either way, what that user may do is resolved against Discord itself.

Snowflakes — guild, user and channel IDs — are strings on the wire. They are 64-bit, and
JavaScript numbers are not.
"""


class Health(BaseModel):
    """Liveness payload, used by the compose healthcheck."""

    status: Literal["ok"]
    version: str


SHUTDOWN_GRACE = 10.0
"""Seconds to let the worker finish the job it is on before cancelling it.

Long enough for a fan-out mid-flight, short enough that a wedged handler cannot hold a
container up past a deployment.
"""


def _start_background(app: FastAPI) -> list[asyncio.Task[None]]:
    """Put the worker and the sweep scheduler on the loop.

    Named tasks, so a traceback says which one died.
    """
    worker: Worker = app.state.worker
    sweeper: Sweeper = app.state.sweeper
    log.info("starting enforcement worker and sweep scheduler")
    return [
        asyncio.create_task(worker.run_forever(), name="timothy-worker"),
        asyncio.create_task(sweeper.run_forever(), name="timothy-sweeper"),
    ]


async def _stop_background(app: FastAPI, tasks: list[asyncio.Task[None]]) -> None:
    """Ask the loops to finish, and only cancel if they will not.

    Asked rather than cancelled: a task cancelled part-way through a transaction cannot
    finish closing its session, and the connection then outlives the engine it came from.
    See :mod:`timothy_api.enforcement.pacing`.
    """
    app.state.worker.stop()
    app.state.sweeper.stop()

    _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE)
    for task in pending:
        log.warning("%s did not stop in time; cancelling", task.get_name())
        task.cancel()
    for task in pending:
        with suppress(asyncio.CancelledError):
            await task


def create_app(
    settings: Settings | None = None,
    *,
    discord_port: DiscordPort | None = None,
    oauth_port: OAuthPort | None = None,
) -> FastAPI:
    """Build the application.

    Args:
        settings: process configuration. Read from the environment when omitted.
        discord_port: the door to Discord. A real, lazily logged-in adapter when omitted.
        oauth_port: the login flow. A real one over HTTPS to Discord when omitted.
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

        login = oauth_port if oauth_port is not None else DiscordOAuth.create(resolved)

        self_unbans = SelfUnbans()
        enforcer = Enforcer(discord=port, settings=resolved, self_unbans=self_unbans)
        context = JobContext(sessions=database.sessions, enforcer=enforcer, settings=resolved)

        app.state.settings = resolved
        app.state.db = database
        app.state.discord = port
        app.state.oauth = login
        app.state.resolver = PermissionResolver(port, ttl=resolved.permission_cache_ttl)
        app.state.self_unbans = self_unbans
        app.state.refresh_queue = RefreshQueue()
        app.state.enforcer = enforcer
        app.state.worker = Worker(context)
        app.state.sweeper = Sweeper(database.sessions, resolved)

        background = _start_background(app) if resolved.workers_enabled else []
        try:
            yield
        finally:
            if background:
                await _stop_background(app, background)
            await database.dispose()
            if isinstance(port, DiscordAdapter):
                await port.close()
            if isinstance(login, DiscordOAuth):
                await login.close()

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

    # Outside the gate, because a browser arriving for the first time has no credential
    # to present — getting one is what these routes are for. See `routers.auth`.
    app.include_router(auth.router)
    app.include_router(routers.api, dependencies=[Depends(authenticate)])
    return app


app = create_app()
