"""Starting the backend: what it tells the logging setup before it serves anything.

None of this is reachable through the application object, which is why it has its own
module. The bug it guards against — a process that runs perfectly and logs nothing useful
— does not fail a single request, so nothing else in this suite would notice it.
"""

from pathlib import Path
from typing import Any

import pytest

from timothy_api import __main__


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run `main` with uvicorn and the logging setup stood in for."""
    recorded: dict[str, Any] = {}
    monkeypatch.setattr(
        __main__.logs,
        "configure",
        lambda service, **kwargs: recorded.update(kwargs, service=service),
    )
    monkeypatch.setattr(
        __main__.uvicorn, "run", lambda _app, **kwargs: recorded.update(uvicorn=kwargs)
    )
    return recorded


def test_logging_is_configured_before_the_server_starts(
    started: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TIMOTHY_LOG_LEVEL", "debug")
    monkeypatch.setenv("TIMOTHY_LOG_DIR", str(tmp_path))

    __main__.main()

    assert started["service"] == "backend"
    assert started["level"] == "debug"
    assert started["log_dir"] == tmp_path


def test_every_credential_this_process_holds_is_registered(
    started: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0014: exact-value redaction is the half that catches a token a library
    interpolated into a message that labels it as nothing.

    It only works if this list is complete, and this is the only place that can say so.
    """
    monkeypatch.setenv("TIMOTHY_DISCORD_TOKEN", "discord-token-value")
    monkeypatch.setenv("TIMOTHY_INTERNAL_TOKEN", "internal-token-value")
    monkeypatch.setenv("TIMOTHY_DISCORD_CLIENT_SECRET", "client-secret-value")

    __main__.main()

    assert set(started["secrets"]) == {
        "discord-token-value",
        "internal-token-value",
        "client-secret-value",
    }


def test_uvicorn_is_not_allowed_to_configure_logging(started: dict[str, Any]) -> None:
    """Uvicorn's default config installs its own handlers on `uvicorn.access` and
    `uvicorn.error` and turns off propagation — which would route the access log and
    every ASGI traceback around the file handler that was just installed."""
    __main__.main()

    assert started["uvicorn"]["log_config"] is None
