"""The backend's database lifecycle.

One engine per process, built at startup and disposed at shutdown, because the backend
is SQLite's sole writer (ADR 0003). Migrations run at startup too: the image ships the
revisions inside the wheel, so a container coming up on a fresh volume brings the schema
with it and there is no separate migrate step to forget.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from timothy_core.db.engine import make_engine, make_sessionmaker
from timothy_core.migrations import upgrade_to_head

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


class Database:
    """An engine, its session factory, and the migrations that shaped it."""

    def __init__(self, url: str) -> None:
        """Build the engine. Nothing is connected to until the first session."""
        self.url = url
        self.engine: AsyncEngine = make_engine(url)
        self.sessions: async_sessionmaker[AsyncSession] = make_sessionmaker(self.engine)

    async def migrate(self) -> None:
        """Bring the schema to head.

        Alembic is synchronous and this runs inside a running loop, so it goes to a
        thread.
        """
        await asyncio.to_thread(upgrade_to_head, self.url)

    async def dispose(self) -> None:
        """Close every pooled connection."""
        await self.engine.dispose()
