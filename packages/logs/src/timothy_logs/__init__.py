"""One logging setup, shared by every Python process Timothy runs.

Three things this arranges, and each of them exists because of a way debugging went
wrong rather than because of a convention:

**The logs outlive the container.** `docker compose logs` reads a buffer the daemon
throws away when the container is recreated, so the traceback that explains last
Tuesday's failure is gone by the time anyone asks about it. Every process here writes a
rotating file into `TIMOTHY_LOG_DIR`, which compose bind-mounts from `./logs` on the
host — one directory holding the backend, the bot, nginx and the tunnel together, so a
single `grep` covers the whole stack.

**Nothing is lost to a handler that does not exist.** Uvicorn configures its own loggers
and leaves the root alone; a library that raises on a thread, an exception that kills the
event loop, and an unhandled error on the way out of `main` all go somewhere else again.
:func:`configure` claims the root logger and then hooks every one of those escape routes,
so "it crashed and the log says nothing" stops being a possible outcome.

**Secrets do not reach the disk.** A log file is copied into a bug report, pasted into a
chat, and read by whoever has the host — none of which is true of the process's
environment. Redaction happens in the formatter, at the last possible moment
(:class:`Redactor`), because that is the only place that sees the message, its arguments
and the traceback as the single string that is actually written.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import TracebackType

MAX_BYTES: Final = 10 * 1024 * 1024
"""Bytes per log file before it rolls. Ten megabytes is a few hundred thousand lines —
enough that an incident is usually inside one file, small enough to open in an editor."""

BACKUP_COUNT: Final = 9
"""Rolled files kept behind the live one, so the directory tops out at 100MB per service.
A bounded ceiling matters more than depth here: this is a bind mount on somebody's
machine, and a disk filled by logging is an outage caused by the thing meant to explain
outages."""

CONSOLE_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
"""What stdout shows under `TIMOTHY_LOG_FORMAT=console`. A terminal gets a sentence."""

FORMATS: Final = ("console", "json")
"""How stdout is written, spelled as in `TIMOTHY_LOG_FORMAT`.

`console` is the default because a bare `timothy-api` outside compose is read by a person.
`json` is what compose sets: the collector needs fields, not sentences, and JSON on stdout
is what makes a line queryable once it reaches VictoriaLogs (ADR 0015). The cost is that
`docker compose logs backend` becomes JSON, which `| jq -r .message` undoes.

Unrecognised values fall back to `console` rather than raising. Logging is the thing that
explains a bad configuration; it is a poor choice of thing to have refuse to start over
one."""

REDACTED: Final = "[REDACTED]"

MIN_SECRET_LENGTH: Final = 6
"""Shorter registered values are ignored. A secret that happens to be `dev` would blank
every occurrence of those three letters anywhere in the logs, which destroys far more
than it protects. Anything this short is not a credential worth having."""

_SECRET_WORDS: Final = (
    "token",
    "secret",
    "password",
    "passwd",
    "pwd",
    "api[_-]?key",
    "apikey",
    "authorization",
    "auth",
    "credential",
    "session",
    "cookie",
    "signature",
)

PATTERNS: Final = (
    # `Authorization: Bearer x`, and bare bearer credentials wherever they appear.
    re.compile(r"(?i)\bbearer\s+[\w.\-+/=~]+"),
    # A Discord bot token: three dot-separated base64url runs, of fixed-ish lengths.
    # Matched on shape because the bot's token reaches the logs through discord.py's own
    # error messages, where it is not labelled as anything.
    re.compile(r"\b[\w-]{23,28}\.[\w-]{6,7}\.[\w-]{27,}\b"),
    # A JWT, same reasoning.
    re.compile(r"\bey[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b"),
    # `token=x`, `"client_secret": "x"`, `password = x` — the labelled cases, in the
    # spellings that show up in query strings, JSON and repr() alike.
    re.compile(
        r"(?i)([\"']?[\w-]*(?:" + "|".join(_SECRET_WORDS) + r")[\w-]*[\"']?\s*[:=]\s*)"
        r"[\"']?([^\s\"',;&})\]]+)",
    ),
    # The OAuth authorization code, which is a credential for the sixty seconds it
    # lives and arrives as a query parameter nginx and uvicorn both log by default.
    re.compile(r"(?i)([?&](?:code|state)=)[^&\s\"']+"),
)
"""Shape-based redaction, applied after the exact secret values.

Deliberately over-broad. A redacted line still says which logger, which level and which
message; a leaked token is permanent. The one thing the patterns must not do is match
something that is *usually* a secret and *sometimes* an identifier — which is why no
pattern here fires on bare digits, and why Discord snowflakes survive intact.
"""


class Redactor:
    """Removes credentials from a line on its way to a handler.

    Two passes, in this order. First the exact values the process was started with —
    :meth:`add` takes them straight off the settings object, so the bot token is caught
    even when it appears inside a URL, a `repr`, or a library's exception message that
    labels it as nothing at all. Then :data:`PATTERNS`, for the credentials this process
    never held: another service's key in a response body, an OAuth code in a query
    string.

    Exact-value redaction is the half that actually matters, and it is the half that only
    works if callers register what they hold. :func:`configure` takes the secrets as an
    argument for that reason — so registering them is part of setting logging up rather
    than something to remember afterwards.
    """

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        """Start with `secrets` registered as exact values."""
        self._secrets: list[str] = []
        self._lock = threading.Lock()
        self.add(*secrets)

    def add(self, *values: str | None) -> None:
        """Register exact values to blank out wherever they appear.

        Empty, absent and implausibly short values are dropped rather than registered:
        see :data:`MIN_SECRET_LENGTH`. Sorted longest-first so that a value containing
        another is replaced whole instead of being left as a redacted fragment plus a
        readable tail.
        """
        with self._lock:
            for value in values:
                if value and len(value) >= MIN_SECRET_LENGTH and value not in self._secrets:
                    self._secrets.append(value)
            self._secrets.sort(key=len, reverse=True)

    def scrub(self, text: str) -> str:
        """Return `text` with every credential it can recognise replaced."""
        with self._lock:
            secrets = tuple(self._secrets)
        for secret in secrets:
            text = text.replace(secret, REDACTED)
        for pattern in PATTERNS:
            text = pattern.sub(_replacement, text)
        return text


def _replacement(match: re.Match[str]) -> str:
    """Keep whatever the pattern captured as context, blank the rest.

    A pattern with a group captures the *label* — `token=`, `?code=` — and a line reading
    `client_secret=[REDACTED]` is far more useful when debugging than one where the whole
    assignment vanished.
    """
    return (match.group(1) if match.lastindex else "") + REDACTED


class RedactingFormatter(logging.Formatter):
    """A formatter that scrubs its own output.

    Subclassed at the formatter rather than filtered at the record because a
    `logging.Filter` sees `msg` and `args` separately and never sees the traceback at
    all — and a traceback is exactly where a token ends up, in the repr of the arguments
    to the frame that raised.
    """

    def __init__(self, redactor: Redactor, fmt: str | None = None) -> None:
        """Format with `fmt`, then hand the result to `redactor`."""
        super().__init__(fmt)
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        """Render the record, redacted."""
        return self.redactor.scrub(super().format(record))


_RESERVED: Final = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}
"""Attributes every record has. Whatever is left was passed as `extra=` and belongs in
the JSON line — that is how a handler logs `guild_id` beside its message and stays
greppable."""


class JsonFormatter(RedactingFormatter):
    """One JSON object per line — always the file, and stdout under `json`.

    JSON rather than the console's sentence because this is read by a machine as often as
    by eye: `jq` over the file, and LogsQL over the store the collector posts it to.
    "every ERROR from the worker on the 3rd" is a filter over fields, not a regex over
    prose. The exception, when there is one, carries its full traceback as a single string
    field, so a stack trace stays one hit rather than forty.

    The field names are the ones VictoriaLogs is told to index: `ts` becomes `_time`,
    `message` becomes `_msg`, and `service` is a stream field (ADR 0015). Renaming any of
    those three means changing the collector's insert parameters to match.
    """

    def __init__(self, redactor: Redactor, service: str) -> None:
        """Tag every line with `service`, so one directory can hold several."""
        super().__init__(redactor)
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        """Render the record as a JSON line, redacted."""
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info))
        if record.stack_info:
            payload["stack"] = record.stack_info
        payload["source"] = f"{record.pathname}:{record.lineno}"

        extra = {key: value for key, value in record.__dict__.items() if key not in _RESERVED}
        if extra:
            payload["extra"] = extra

        line = json.dumps(payload, default=repr, ensure_ascii=False)
        return self.redactor.scrub(line)


def configure(
    service: str,
    *,
    level: str = "INFO",
    log_dir: Path | str | None = None,
    log_format: str = "console",
    secrets: Iterable[str] = (),
) -> Redactor:
    """Set up logging for this process and return the redactor it installed.

    Idempotent by demolition: any handler already on the root logger is removed first, so
    a second call — a test, a reload — replaces the setup rather than doubling every line.

    Args:
        service: which process this is, used for the filename and the `service` field.
        level: the root level, spelled as in `TIMOTHY_LOG_LEVEL` — case does not matter.
        log_dir: where the file goes. `None`, or a directory that cannot be written to,
            leaves the console handler as the only one: a log directory that is missing
            or read-only is a deployment mistake, and a process that refuses to start
            over it is a worse outcome than one that says so and carries on to stdout.
        log_format: how stdout is written — see :data:`FORMATS`. Does not affect the
            file, which is always JSON.
        secrets: exact values to blank out wherever they appear. Pass everything the
            process holds — see :class:`Redactor`.

    Returns:
        The installed :class:`Redactor`, so later-discovered secrets can be added to it.
    """
    redactor = Redactor(secrets)
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()
    root.setLevel(level.upper())

    console = logging.StreamHandler(sys.stdout)
    if log_format.lower() == "json":
        console.setFormatter(JsonFormatter(redactor, service))
    else:
        console.setFormatter(RedactingFormatter(redactor, CONSOLE_FORMAT))
    root.addHandler(console)

    handler = _file_handler(service, log_dir, redactor)
    if handler is not None:
        root.addHandler(handler)
    else:
        logging.getLogger(__name__).warning(
            "no log directory: logs are console-only and will not survive this container"
        )

    _install_hooks()
    return redactor


def _file_handler(
    service: str, log_dir: Path | str | None, redactor: Redactor
) -> logging.Handler | None:
    """A rotating JSON file in `log_dir`, or `None` if it cannot be opened."""
    if log_dir is None:
        return None
    try:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            directory / f"{service}.log",
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        return None
    handler.setFormatter(JsonFormatter(redactor, service))
    return handler


def _install_hooks() -> None:
    """Route the three ways an exception escapes logging entirely into the root logger.

    Without these, the process dies and the file that was supposed to say why holds
    everything up to the last line before the crash. Python prints the traceback to
    stderr instead, which the daemon keeps only until the container is recreated — the
    thing this whole module exists to stop relying on.
    """
    log = logging.getLogger("timothy.unhandled")

    def on_exception(
        kind: type[BaseException], value: BaseException, tb: TracebackType | None
    ) -> None:
        if issubclass(kind, KeyboardInterrupt):  # pragma: no cover — Ctrl-C is not a fault
            sys.__excepthook__(kind, value, tb)
            return
        log.critical("unhandled exception", exc_info=(kind, value, tb))

    def on_thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:  # pragma: no cover
            return
        log.critical(
            "unhandled exception in thread %s",
            args.thread.name if args.thread else "?",
            # The exception itself rather than the triple: `exc_value` is typed as
            # optional, and `logging` reconstructs the triple from an exception anyway.
            exc_info=args.exc_value,
        )

    def on_unraisable(args: sys.UnraisableHookArgs) -> None:
        """A `__del__` that raised, or a weakref callback that failed.

        asyncio already routes "exception was never retrieved" through `logging`; this is
        the rest of the same family. Both are how a resource leak announces itself, and
        both are invisible in a container nobody is watching.
        """
        log.error("unraisable exception in %r", args.object, exc_info=args.exc_value)

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception
    sys.unraisablehook = on_unraisable
