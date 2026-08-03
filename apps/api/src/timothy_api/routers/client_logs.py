"""Where the browser's own crashes go.

nginx logs which requests the SPA made; it cannot log a `TypeError` thrown while
rendering, because that happens on somebody's laptop and never touches the origin. So a
React crash left no trace anywhere on the host — the screen went blank, and the only
record was in a devtools console that had been closed by the time anyone asked. This is
the route that closes that gap: the SPA reports its unhandled errors here, and they land
in the same log file as everything the backend and the bot write.

Three deliberate restrictions, because this is the one place a client dictates what gets
written to Timothy's disk:

* **Authenticated like everything else.** The router sits behind the API's gate, so a
  report costs a valid session — a member of the management guild (ADR 0013) — or the
  internal token. There is no anonymous write path here.
* **Bounded per caller.** :class:`Budget` caps how many reports one actor can file per
  minute. A render loop that throws on every frame is exactly the failure this route is
  for, and it is also the one that would fill the disk while doing it.
* **Never above `error`.** A client's own claim about severity decides nothing else, and
  a browser cannot declare a `CRITICAL`. What arrives is a report *about* the client, not
  a log line from a trusted process.

The message and stack are redacted on the way out like every other line, by the formatter
in :mod:`timothy_logs` — the SPA holds no secrets, but a stack trace can carry a URL
somebody put a token in.
"""

import logging
import time
from typing import Annotated, Final, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from timothy_api.identity import CallerDep

router = APIRouter(prefix="/client-logs", tags=["client-logs"])

log = logging.getLogger("timothy.web")
"""Deliberately not `__name__`. These lines are *about* the browser, and a reader
filtering the log file on `timothy.web` wants the client's errors, not this module's."""

LEVELS: Final = {"warning": logging.WARNING, "error": logging.ERROR}
"""What a client may claim. Nothing below `warning`, because a browser sending its debug
output to the server is a different feature with a different budget."""

MAX_MESSAGE: Final = 2_000
MAX_STACK: Final = 16_000
"""A minified bundle's stack is long, and truncating it to something tidy throws away the
frames the error actually came from. Sixteen kilobytes holds a deep React stack whole."""

BUDGET_PER_MINUTE: Final = 30
"""Reports one actor may file a minute. A person clicking around produces none; a broken
render produces thousands, and this is what stands between that and the disk."""


class ClientLogEntry(BaseModel):
    """One unhandled error from the browser."""

    level: Literal["warning", "error"] = "error"
    message: Annotated[str, Field(max_length=MAX_MESSAGE)]
    stack: Annotated[str | None, Field(default=None, max_length=MAX_STACK)]
    url: Annotated[str | None, Field(default=None, max_length=2_000)]
    """The route the SPA was on. The single most useful field for reproducing it, and the
    one thing an exception message never says."""

    kind: Annotated[str | None, Field(default=None, max_length=100)]
    """How it surfaced: a render boundary, `window.onerror`, a rejected promise."""


class Budget:
    """A fixed-window cap on how much one actor may write, per process.

    In memory rather than in the database on purpose: this protects the disk from a
    runaway client, and a limiter that writes a row per rejected report would be the leak
    it is meant to plug. One backend process holds every request (ADR 0003), so a
    per-process window is a per-deployment window.
    """

    WINDOW: Final = 60.0

    def __init__(self, limit: int = BUDGET_PER_MINUTE) -> None:
        """Allow `limit` reports per actor per minute."""
        self.limit = limit
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        """Whether `key` has room left in the current window."""
        now = time.monotonic()
        started, used = self._windows.get(key, (now, 0))
        if now - started >= self.WINDOW:
            started, used = now, 0
        if used >= self.limit:
            self._windows[key] = (started, used)
            return False
        self._windows[key] = (started, used + 1)
        return True


_budget = Budget()


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def report_client_error(
    entry: ClientLogEntry, caller: CallerDep, request: Request
) -> None:
    """Record an error the browser could not handle.

    Answers 202 rather than 204: the report is written, and nothing about the client's
    situation depends on what happened to it. A client that has just crashed should not
    also have to handle a failure to say so.

    Raises:
        HTTPException: 429 once this actor's budget for the minute is spent.
    """
    if not _budget.allow(str(caller.actor)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many client error reports; slow down",
        )

    log.log(
        LEVELS[entry.level],
        "client error: %s",
        entry.message,
        extra={
            "actor": str(caller.actor),
            "client_url": entry.url,
            "client_kind": entry.kind,
            "client_stack": entry.stack,
            "user_agent": request.headers.get("user-agent"),
        },
    )
