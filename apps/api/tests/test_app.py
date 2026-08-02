"""Startup: what the process builds for itself when nothing is injected."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from timothy_api.app import create_app
from timothy_api.discord_adapter import DiscordAdapter
from timothy_api.settings import Settings
from timothy_core.db.models import Base
from timothy_core.migrations import sync_url


def test_starting_up_migrates_an_empty_database(settings: Settings) -> None:
    """The revisions travel inside the wheel, so a container coming up on a fresh volume
    brings its own schema and there is no migrate step to forget."""
    with TestClient(create_app(settings, discord_port=None)) as client:
        assert client.get("/health").status_code == 200

    engine = create_engine(sync_url(settings.database_url))
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert set(Base.metadata.tables) <= tables


def test_a_real_adapter_is_built_when_none_is_given(settings: Settings) -> None:
    """The injected port is for the tests; production wires the real one, and wires it
    without reaching Discord at startup."""
    with TestClient(create_app(settings)) as client:
        port = client.app.state.discord  # ty: ignore[unresolved-attribute]

        assert isinstance(port, DiscordAdapter)
        assert client.get("/health").status_code == 200


def test_settings_are_read_from_the_environment_when_none_are_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create_app()` with no arguments is what `timothy_api.app:app` does at import."""
    monkeypatch.setenv("TIMOTHY_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'env.db'}")
    monkeypatch.setenv("TIMOTHY_MANAGEMENT_GUILD_ID", "12345")

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.settings.management_guild_id == 12345  # ty: ignore[unresolved-attribute]
