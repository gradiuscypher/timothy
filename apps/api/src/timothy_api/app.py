"""FastAPI application factory."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from timothy_api import __version__


class Health(BaseModel):
    """Liveness payload, used by the compose healthcheck."""

    status: Literal["ok"]
    version: str


def create_app() -> FastAPI:
    """Build the application. No domain routes yet — phase 0 is scaffolding."""
    app = FastAPI(
        title="Timothy",
        version=__version__,
        description="Shared moderation service for Discord.",
    )

    @app.get("/health")
    async def health() -> Health:
        return Health(status="ok", version=__version__)

    return app


app = create_app()
