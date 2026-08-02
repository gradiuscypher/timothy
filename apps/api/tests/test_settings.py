"""Configuration, and the one setting that must never fail closed the wrong way."""

from datetime import timedelta

import pytest

from timothy_api.settings import Settings

DEFAULT_BURST = 25


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off", " f "])
def test_dry_run_can_be_switched_off_deliberately(raw: str) -> None:
    assert Settings(dry_run=raw).dry_run is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", "on"])
def test_dry_run_reads_the_obvious_affirmatives(raw: str) -> None:
    assert Settings(dry_run=raw).dry_run is True


@pytest.mark.parametrize("raw", ["", "  ", "flase", "maybe", "# off", "2"])
def test_an_unreadable_dry_run_means_on(raw: str) -> None:
    """ADR 0007: the flag guards banning real people, and a typo must not be the thing
    that switches it off. Refusing to start would be worse — a backend that is not
    running is a backend that is not enforcing either."""
    assert Settings(dry_run=raw).dry_run is True


def test_dry_run_defaults_to_on() -> None:
    assert Settings().dry_run is True


def test_a_dry_run_that_is_not_even_a_string_means_on() -> None:
    assert Settings(dry_run=object()).dry_run is True  # ty: ignore[invalid-argument-type]


def test_the_defaults_match_plan_md() -> None:
    settings = Settings()

    assert settings.enforcement_burst_limit == DEFAULT_BURST
    assert settings.sweep_interval == timedelta(hours=1)
    assert settings.permission_cache_ttl == timedelta(seconds=60)
    assert settings.auto_subscribe_pool == "global"


def test_nothing_is_configured_open_by_default() -> None:
    """An unset management guild closes pool management rather than opening it: nobody
    holds `ADMINISTRATOR` in guild zero."""
    settings = Settings()

    assert settings.management_guild_id == 0
    assert settings.internal_token.get_secret_value() == ""


def test_secrets_do_not_leak_into_a_repr() -> None:
    assert "hunter2" not in repr(Settings(internal_token="hunter2", discord_token="hunter2"))
