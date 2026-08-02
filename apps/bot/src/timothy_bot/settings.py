"""Process configuration, read from the environment."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bot process settings."""

    model_config = SettingsConfigDict(env_prefix="TIMOTHY_", frozen=True)

    discord_token: SecretStr = SecretStr("")
    api_base_url: str = "http://backend:8000"
    log_level: str = "INFO"
