"""Shared fixtures. Every database test gets a real SQLite file, built by migrations."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from timothy_core.db.engine import make_engine, make_sessionmaker
from timothy_core.migrations import sync_url, upgrade_to_head


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """A migrated, empty database of this test's own."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'timothy.db'}"
    upgrade_to_head(url)
    return url


@pytest.fixture
def sync_engine(database_url: str) -> Iterator[Engine]:
    """A plain engine for inspecting the schema.

    Disposed explicitly: a pooled SQLite connection collected by the garbage collector
    raises during finalisation, and `filterwarnings = ["error"]` turns that into a
    failure in whichever unrelated test happens to run next.
    """
    engine = create_engine(sync_url(database_url))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(database_url)
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()
