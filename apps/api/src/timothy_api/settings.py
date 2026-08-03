"""Process and domain configuration, read from the environment.

Every setting in PLAN.md's Configuration table lives here, under the `TIMOTHY_` prefix
phase 0 established — `MANAGEMENT_GUILD_ID` is `TIMOTHY_MANAGEMENT_GUILD_ID`, and so on.

Two of the types here exist because of how they fail rather than how they parse.
`FailSafeFlag` reads anything it does not recognise as *on*, so a typo in `DRY_RUN` stops
Timothy acting rather than starting it. `Duration` accepts the plain seconds that
`compose.yaml` and `.env.example` have always documented; without it those documented
values stop the backend from starting at all.
"""

from datetime import timedelta
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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


def _seconds_or_iso(value: object) -> object:
    """Read a duration written as a plain number of seconds.

    `.env.example` and `compose.yaml` have always documented these as "seconds, or ISO
    8601", and `TIMOTHY_SWEEP_INTERVAL=604800` is the value compose defaults to. Pydantic's
    own `timedelta` parsing rejects a bare number in a string, so without this the
    documented configuration stops the backend from starting at all — which is how it was
    found: by running the stack rather than by reading it.

    Anything that is not a bare number is handed on untouched, so `PT1H` and `01:00:00`
    still parse as they did.
    """
    if isinstance(value, str):
        try:
            return timedelta(seconds=float(value.strip()))
        except ValueError:
            return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return timedelta(seconds=value)
    return value


Duration = Annotated[timedelta, BeforeValidator(_seconds_or_iso)]
"""A `timedelta` that also accepts a plain number of seconds, in either direction."""


def _snowflake_set(value: object) -> object:
    """Read a comma-separated list of Discord IDs.

    `NoDecode` below turns off pydantic-settings' habit of JSON-parsing complex types
    from the environment, which would make this setting `["1","2"]` in a `.env` file.
    Every other list-shaped thing an operator writes in this project is comma-separated,
    and a JSON array in an environment variable is a quoting problem waiting to happen.

    Anything that is not a run of digits is dropped rather than raising. Every setting
    that uses this is a *narrowing* one — a typo produces a smaller set of owners, or of
    roles that may manage pools, never a larger one — so failing closed on the bad entry
    is safer than refusing to start.
    """
    if isinstance(value, str):
        return frozenset(
            int(part)
            for part in (piece.strip() for piece in value.split(","))
            if part.isdigit()
        )
    return value


SnowflakeSet = Annotated[frozenset[int], NoDecode, BeforeValidator(_snowflake_set)]
"""Discord IDs, written `123,456`. `frozenset` because `Settings` is frozen and a
mutable field would make the model unhashable."""


def _optional_path(value: object) -> object:
    """Read a path that an empty setting turns off rather than points at `.`.

    `TIMOTHY_LOG_DIR=` is how a bare `timothy-api` outside compose says "no log file".
    Left to pydantic, that empty string parses as `Path(".")` and the process quietly
    writes its logs into whatever directory it happens to have been started from.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


LogDir = Annotated[Path | None, BeforeValidator(_optional_path)]


class Settings(BaseSettings):
    """Backend process settings."""

    model_config = SettingsConfigDict(env_prefix="TIMOTHY_", frozen=True)

    # -- process -------------------------------------------------------------

    host: str = "0.0.0.0"  # noqa: S104 — bound inside the compose network only, never published
    port: int = 8000
    log_level: str = "info"
    database_url: str = "sqlite+aiosqlite:////data/timothy.db"

    log_dir: LogDir = Path("/logs")
    """Where the rotating log file goes, alongside every other service's.

    Compose bind-mounts `./logs` here, so the file survives the container being recreated
    — which the daemon's own buffer does not, and which is the whole reason this exists.
    Empty turns the file off and leaves logging on stdout, for a test or a bare
    `timothy-api` outside compose.
    """

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

    # -- browser sessions ----------------------------------------------------

    discord_client_id: str = ""
    """The Discord application's client ID, for the OAuth login the web UI uses.

    Empty closes login rather than opening it: `/auth/login` answers 503 and says what is
    missing. The bot token above is a different credential for a different purpose — it
    is Timothy acting, this is a person proving who they are.
    """

    discord_client_secret: SecretStr = SecretStr("")

    public_base_url: str = ""
    """Where a browser reaches Timothy, e.g. `https://timothy.example.com`.

    Discord requires the redirect URI to be registered up front and to match exactly, so
    it cannot be reconstructed from the incoming request: behind the tunnel and nginx the
    backend sees an internal host and, without this, would send people back to a hostname
    that does not resolve for them.
    """

    session_lifetime: Duration = timedelta(days=7)
    session_cookie_secure: bool = True
    """Whether the session cookie is `Secure`.

    On in production, where Cloudflare Tunnel terminates TLS and the origin is only ever
    reached over HTTPS. The only reason to turn it off is a local stack served over plain
    HTTP, and a browser silently dropping the cookie is a confusing way to discover that.
    """

    # -- domain --------------------------------------------------------------

    management_guild_id: int = 0
    """The one guild pool authority is held in (ADR 0001). Zero means unconfigured, and
    nobody holds a role in guild zero, so pool management is closed until it is set.

    Also the web UI's front door: a browser session is issued only to a member of this
    guild (ADR 0013). Membership alone — the roles above are what authority is derived
    from, this is only who may sign in. Zero closes login as well, and says so at
    `/auth/login` rather than refusing everybody after the round trip to Discord.
    """

    pool_manager_role_ids: SnowflakeSet = frozenset()
    """Roles in the management guild whose holders own pools and listings (ADR 0012).

    Written as `TIMOTHY_POOL_MANAGER_ROLE_IDS=1234567890`, comma-separated if more than
    one role should have it.

    Empty closes pool management for everybody, including the management guild's
    administrators and its owner. It never falls back to them: administering that guild
    and curating a ban list that reaches every subscribing guild are different jobs, and
    a fallback would silently make them the same one again — the same reasoning as
    `owner_ids` below (ADR 0011).

    Deploying this for the first time therefore needs the role created and assigned
    before pool management works. That is the intended shape: an explicit grant, visible
    in Discord's own role list, rather than a permission someone already had.
    """

    owner_ids: SnowflakeSet = frozenset()
    """Whoever runs this deployment. The operations view and nothing else (ADR 0011).

    Written as `TIMOTHY_OWNER_IDS=242024455190577152`, or a comma-separated list if more
    than one person needs it. Usually one.

    Empty closes the operations view for everybody, including the management guild's
    administrators. It never falls back to them: the whole point of this setting is that
    "administers the pool server" and "runs Timothy" are different jobs, and a fallback
    would silently make them the same one again.
    """

    permission_cache_ttl: Duration = timedelta(seconds=60)

    auto_subscribe_pool: str = "global"
    """The pool a guild is subscribed to when Timothy joins it (ADR 0002).

    A join-time default, not a reserved name: the subscription it creates is an ordinary
    row that the guild's administrators can change the level of or delete outright, which
    is the whole point of that ADR. Empty disables the behaviour.
    """

    # -- enforcement ---------------------------------------------------------

    dry_run: FailSafeFlag = True
    """Record every enforcement, issue nothing to Discord (ADR 0007).

    Dry run writes to the audit log and deliberately **not** to `enforcement_outcomes`:
    an outcome is an attribution claim that reverting acts on, and a `banned` row for a
    ban that was never issued would have Timothy unban a user it never touched. See
    :mod:`timothy_api.enforcement.engine`.
    """

    enforcement_burst_limit: int = Field(default=25, gt=0)
    sweep_interval: Duration = timedelta(days=7)
    """How often to start a round of sweeps.

    Has to be longer than a round takes, or the round never ends and the interval means
    nothing: a guild with a sweep still outstanding is skipped, so a short interval does
    not sweep more often, it just leaves the worker permanently busy.

    A round is one member lookup per listed user per subscribed guild, issued serially at
    about two a second — for the migrated data, ~347,000 lookups and roughly 48 hours.
    Weekly is that with room around it. Measure before lowering it.
    """

    # -- the worker's own machinery ------------------------------------------

    workers_enabled: bool = True
    """Whether the lifespan starts the job worker and the sweep scheduler.

    On in production — the backend is where enforcement runs (ADR 0003). Off in most
    tests, which drive :meth:`~timothy_api.enforcement.worker.Worker.run_once` directly
    so that what ran, and when, is not a matter of timing.
    """

    job_poll_interval: Duration = timedelta(seconds=1)
    """How long the worker waits after finding the queue empty. Enforcement is
    immediate (ADR 0004), so this is the floor on how immediate."""

    job_max_attempts: int = Field(default=5, gt=0)
    """Attempts before a job is abandoned as `failed`. Per-guild failures are recorded
    as enforcement outcomes and retried by the sweep instead, so reaching this means the
    job itself could not run at all."""
