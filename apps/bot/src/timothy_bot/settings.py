"""Process configuration, read from the environment."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bot process settings."""

    model_config = SettingsConfigDict(env_prefix="TIMOTHY_", frozen=True)

    discord_token: SecretStr = SecretStr("")
    api_base_url: str = "http://backend:8000"
    log_level: str = "INFO"

    internal_token: SecretStr = SecretStr("")
    """What the bot presents to the backend on every call. The bot asserts identity —
    whose interaction this is — and never authority; the backend resolves that itself
    (ADR 0003). Phase 4 is what starts sending it."""
