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
    assert settings.sweep_interval == timedelta(days=7)
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


def test_a_duration_may_be_plain_seconds() -> None:
    """The form `compose.yaml` and `.env.example` document, and the form compose defaults
    to. Pydantic's own `timedelta` parsing rejects it, so without the `Duration` validator
    the documented configuration stops the backend from starting."""
    settings = Settings(
        permission_cache_ttl="60",
        sweep_interval="604800",
        job_poll_interval="1",
    )

    assert settings.permission_cache_ttl == timedelta(seconds=60)
    assert settings.sweep_interval == timedelta(days=7)
    assert settings.job_poll_interval == timedelta(seconds=1)


def test_a_duration_may_still_be_iso_8601() -> None:
    """The other half of what the documentation promises."""
    assert Settings(sweep_interval="PT90M").sweep_interval == timedelta(minutes=90)


def test_the_whole_compose_environment_starts_the_process() -> None:
    """Every default `compose.yaml` supplies, parsed together. This is the check that was
    missing when the duration bug shipped: each setting was tested, the file they are
    written in was not."""
    settings = Settings(
        database_url="sqlite+aiosqlite:////data/timothy.db",
        discord_token="x",
        internal_token="x",
        management_guild_id="1",
        dry_run="true",
        auto_subscribe_pool="global",
        permission_cache_ttl="60",
        sweep_interval="604800",
        enforcement_burst_limit="25",
        workers_enabled="true",
        job_poll_interval="1",
        job_max_attempts="5",
    )

    assert settings.sweep_interval == timedelta(days=7)
    assert settings.workers_enabled is True


def test_a_duration_given_as_a_number_is_seconds() -> None:
    """`.env` hands these over as strings, but nothing stops a caller constructing
    `Settings` in Python — and a plain number there meaning nothing would be a surprise
    in the same shape as the one that stopped the container from starting."""
    assert Settings(sweep_interval=3600).sweep_interval == timedelta(hours=1)
    assert Settings(permission_cache_ttl=1.5).permission_cache_ttl == timedelta(seconds=1.5)


def test_owner_ids_are_written_the_way_every_other_list_here_is() -> None:
    """Comma-separated, not JSON. An environment variable holding `["1","2"]` is a
    quoting problem waiting to happen, and nothing else in this project asks for one."""
    assert Settings(owner_ids="242024455190577152").owner_ids == frozenset({242024455190577152})
    assert Settings(owner_ids=" 1 , 2 ,3 ").owner_ids == frozenset({1, 2, 3})


def test_no_owner_is_the_default() -> None:
    """The operations view is closed until somebody is named. It never falls back."""
    assert Settings().owner_ids == frozenset()


def test_no_pool_manager_role_is_the_default() -> None:
    """Pool management is closed until a role is named, and never falls back to the
    management guild's administrators (ADR 0012). Deploying this the first time means
    creating the role and assigning it, which is the intended shape: an explicit grant
    rather than a permission somebody already had."""
    assert Settings().pool_manager_role_ids == frozenset()


def test_pool_manager_roles_are_a_comma_separated_set() -> None:
    """More than one role can own pools, without merging them in Discord."""
    assert Settings(pool_manager_role_ids="1, 2").pool_manager_role_ids == frozenset({1, 2})


def test_an_unreadable_owner_id_is_dropped_rather_than_fatal() -> None:
    """This setting only ever narrows, so a typo produces a smaller set of owners and
    never a larger one. Failing closed on the bad entry beats refusing to start."""
    assert Settings(owner_ids="1,not-an-id,,2").owner_ids == frozenset({1, 2})
