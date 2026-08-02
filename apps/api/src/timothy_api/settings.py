"""Process configuration, read from the environment.

Only the settings phase 0 needs to stand a container up live here. The domain settings
from PLAN.md — `MANAGEMENT_GUILD_ID`, `DRY_RUN`, `ENFORCEMENT_BURST_LIMIT` and friends —
arrive with the code that reads them.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend process settings."""

    model_config = SettingsConfigDict(env_prefix="TIMOTHY_", frozen=True)

    host: str = "0.0.0.0"  # noqa: S104 — bound inside the compose network only, never published
    port: int = 8000
    log_level: str = "info"
