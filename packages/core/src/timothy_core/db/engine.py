"""Engine and session construction.

The backend is the sole writer (ADR 0003), so the interesting part here is the pragmas:
SQLite ships with foreign keys *off*, and every cascade the schema declares is inert
without them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import DBAPIConnection
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.pool import ConnectionPoolEntry

PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA busy_timeout = 5000",
)


def _apply_pragmas(connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    cursor = connection.cursor()
    try:
        for pragma in PRAGMAS:
            cursor.execute(pragma)
    finally:
        cursor.close()


def make_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async engine with Timothy's pragmas attached to every connection."""
    engine = create_async_engine(url, echo=echo)
    event.listen(engine.sync_engine, "connect", _apply_pragmas)
    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory. `expire_on_commit` stays off so results survive the commit."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
