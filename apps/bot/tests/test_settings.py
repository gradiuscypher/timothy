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
