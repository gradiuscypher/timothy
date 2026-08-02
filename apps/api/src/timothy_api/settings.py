"""Process and domain configuration, read from the environment.

Every setting in PLAN.md's Configuration table lives here, under the `TIMOTHY_` prefix
phase 0 established — `MANAGEMENT_GUILD_ID` is `TIMOTHY_MANAGEMENT_GUILD_ID`, and so on.
Not all of them are read yet: `dry_run`, `enforcement_burst_limit` and `sweep_interval`
are ADR 0007's rails and belong to the enforcement worker in phase 3. They are declared
now so the container's configuration surface is complete and stable, and so `dry_run`'s
fail-safe parsing is settled before anything can be harmed by getting it wrong.
"""

from datetime import timedelta
from typing import Annotated

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

FALSE_WORDS = frozenset({"0", "false", "f", "no", "n", "off"})
"""The only spellings that turn a fail-safe flag off. Deliberately not paired with a set
of true words: anything not on this list reads as on, including nonsense."""


def _fail_safe_true(value: object) -> bool:
    """Parse a flag that must default to on when it cannot be read (ADR 0007).

    Pydantic's own bool parsing raises on anything it does not recognise, and a backend
    that refuses to start is a backend that is not enforcing. Every unrecognised value —
    a typo, an empty string, a comment someone left on the line — therefore reads as
    *on*, which for dry run means Timothy issues nothing to Discord.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in FALSE_WORDS
    return True


FailSafeFlag = Annotated[bool, BeforeValidator(_fail_safe_true)]


class Settings(BaseSettings):
    """Backend process settings."""

    model_config = SettingsConfigDict(env_prefix="TIMOTHY_", frozen=True)

    # -- process -------------------------------------------------------------

    host: str = "0.0.0.0"  # noqa: S104 — bound inside the compose network only, never published
    port: int = 8000
    log_level: str = "info"
    database_url: str = "sqlite+aiosqlite:////data/timothy.db"

    # -- credentials ---------------------------------------------------------

    discord_token: SecretStr = SecretStr("")
    """The bot token. The backend is the only thing that makes Discord REST calls
    (ADR 0003); the bot container holds the same token for the gateway."""

    internal_token: SecretStr = SecretStr("")
    """Shared secret every API caller must present.

    Callers assert identity and the backend resolves authority (ADR 0003), which is only
    safe if the assertion itself is trusted. nginx proxies `/api` from the public tunnel,
    so without this anyone could claim to be an administrator by sending their user ID.
    Empty means the API refuses every request rather than accepting every request.
    """

    # -- domain --------------------------------------------------------------

    management_guild_id: int = 0
    """The one guild whose administrators own pools and listings (ADR 0001). Zero means
    unconfigured, and nobody holds `ADMINISTRATOR` in guild zero, so pool management is
    closed until it is set."""

    permission_cache_ttl: timedelta = timedelta(seconds=60)

    auto_subscribe_pool: str = "global"
    """The pool a guild is subscribed to when Timothy joins it (ADR 0002).

    A join-time default, not a reserved name: the subscription it creates is an ordinary
    row that the guild's administrators can change the level of or delete outright, which
    is the whole point of that ADR. Empty disables the behaviour.
    """

    # -- domain, read by phase 3 ---------------------------------------------

    dry_run: FailSafeFlag = True
    enforcement_burst_limit: int = Field(default=25, gt=0)
    sweep_interval: timedelta = timedelta(hours=1)
