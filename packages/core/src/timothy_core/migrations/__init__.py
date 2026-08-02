"""Alembic migrations, shipped inside the package.

The migration directory travels in the wheel, so the container runs migrations without
a checkout and without caring what the working directory is. `alembic.ini` at the
package root exists for the `alembic` CLI during development; nothing at runtime reads
it.

Alembic is synchronous, so these helpers strip the async driver from the URL and talk to
SQLite directly. Call them from a thread (`asyncio.to_thread`) if an event loop is
already running.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR: Final = Path(__file__).parent
CONFIG_KEY: Final = "sqlalchemy.url"


def sync_url(url: str) -> str:
    """Drop the async driver, which Alembic cannot use."""
    return url.replace("+aiosqlite", "", 1)


def alembic_config(url: str) -> Config:
    """An Alembic config pointed at the packaged migrations."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(CONFIG_KEY, sync_url(url))
    return config


def upgrade_to_head(url: str) -> None:
    """Bring `url`'s database up to the newest revision."""
    command.upgrade(alembic_config(url), "head")


def downgrade_to_base(url: str) -> None:
    """Unwind every revision. Used by the tests that prove the migrations reverse."""
    command.downgrade(alembic_config(url), "base")
