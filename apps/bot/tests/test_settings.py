import pytest
from pydantic import SecretStr

from timothy_bot.settings import Settings


def test_api_base_url_defaults_to_the_compose_service() -> None:
    assert Settings().api_base_url == "http://backend:8000"


def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIMOTHY_API_BASE_URL", "http://localhost:9000")

    assert Settings().api_base_url == "http://localhost:9000"


def test_the_token_does_not_leak_into_logs() -> None:
    settings = Settings(discord_token=SecretStr("hunter2"))

    assert "hunter2" not in repr(settings)


def test_the_internal_token_does_not_leak_into_logs() -> None:
    """It is the whole of the API's authentication, and this object is logged on error."""
    settings = Settings(internal_token=SecretStr("shared-secret"))

    assert "shared-secret" not in repr(settings)


def test_an_unconfigured_management_guild_is_zero() -> None:
    """Which registers the pool commands nowhere. Nobody is an administrator of guild 0,
    so the backend would refuse them anyway — this way a moderator never sees them."""
    assert Settings().management_guild_id == 0


def test_the_bot_reads_the_same_management_guild_as_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One variable, both containers. Registering `/add_ban` in a guild the backend will
    refuse produces a command that is visible and always fails."""
    monkeypatch.setenv("TIMOTHY_MANAGEMENT_GUILD_ID", "100000000000000001")

    assert Settings().management_guild_id == 100_000_000_000_000_001


def test_commands_are_uploaded_by_default() -> None:
    """Registration lives in the bot now, so a deployment that changed a command ships
    it."""
    assert Settings().sync_commands is True


def test_the_gateway_is_on_by_default() -> None:
    """Off is for CI, where the token is a placeholder and there is no application to
    log in to."""
    assert Settings().gateway_enabled is True


def test_the_diagnostics_cadence_matches_what_compose_documents() -> None:
    """The backend reads the same variable to decide when a snapshot has gone stale, so
    the two drifting apart would have administrators told their data was old on the
    schedule the bot was still meeting."""
    assert Settings().diagnostics_interval_seconds == 900.0
    assert Settings(diagnostics_interval_seconds="900").diagnostics_interval_seconds == 900.0


def test_the_backend_timeout_is_inside_discord_s_deadline() -> None:
    """Three seconds is the interaction deadline. An answer that arrives later cannot be
    delivered, so waiting for it only costs the moderator the error message."""
    assert Settings().request_timeout < 3.0


def test_a_timeout_of_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="request_timeout"):
        Settings(request_timeout=0)
