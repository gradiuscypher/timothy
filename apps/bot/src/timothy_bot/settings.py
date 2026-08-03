"""Process configuration, read from the environment."""

from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _optional_path(value: object) -> object:
    """Read a path that an empty setting turns off rather than points at `.`."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


LogDir = Annotated[Path | None, BeforeValidator(_optional_path)]


class Settings(BaseSettings):
    """Bot process settings."""

    model_config = SettingsConfigDict(env_prefix="TIMOTHY_", frozen=True)

    discord_token: SecretStr = SecretStr("")
    api_base_url: str = "http://backend:8000"
    log_level: str = "INFO"

    log_dir: LogDir = Path("/logs")
    """Where the rotating log file goes, in the directory the backend and nginx also
    write to. Empty turns the file off and leaves logging on stdout."""

    log_format: str = "console"
    """How stdout is written: `console` or `json` (see `timothy_logs.FORMATS`). Compose
    sets `json`, for the collector; the default stays readable for a bare process."""

    internal_token: SecretStr = SecretStr("")
    """What the bot presents to the backend on every call. The bot asserts identity —
    whose interaction this is — and never authority; the backend resolves that itself
    (ADR 0003)."""

    management_guild_id: int = 0
    """Where the pool and listing commands are registered.

    The same value the backend reads, and it has to be: registering `/add_ban` in a guild
    where nobody holds pool authority produces a command that is visible and always
    fails. Unset registers them nowhere, which is the safe direction — nobody holds a
    role in guild 0.

    Who may *run* those commands is a role there, not `ADMINISTRATOR` (ADR 0012), and the
    backend is what enforces it. Discord's own default makes them administrator-only
    until somebody points them at that role under Integrations."""

    gateway_enabled: bool = True
    """Connect to Discord at all.

    Off leaves the process up and the backend reachable but never opens the gateway —
    what CI runs, where the token is a placeholder and there is no application to log in
    to. The backend's `WORKERS_ENABLED` is the same idea from the other side."""

    sync_commands: bool = True
    """Upload the command tree to Discord on startup.

    On, because command registration lives in the bot now and a deployment that changed a
    command should ship it. Off for a second instance run against the same application,
    which would otherwise overwrite the live surface with whatever it happens to have."""

    request_timeout: float = Field(default=2.5, gt=0)
    """Seconds to wait for the backend.

    Inside Discord's three-second interaction deadline on purpose. Everything a command
    does is answered well within it — a mutation enqueues its fan-out rather than
    performing it — so a request still outstanding at this point is a backend in trouble,
    and a moderator is better served by an error than by an interaction that expires."""
