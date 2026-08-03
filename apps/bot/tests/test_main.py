"""Starting up: the backend first, then Discord."""

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from support import Backend

from timothy_bot import __main__, api
from timothy_bot.client import TimothyBot
from timothy_bot.settings import Settings

pytestmark = pytest.mark.anyio


async def test_the_backend_is_checked_before_anything_else(
    http: httpx.AsyncClient, backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    """A bot that reached Discord before it could reach the backend would show a live
    command surface that answers every invocation with an error."""
    backend.replies(200, {"status": "ok", "version": "0.1.0"})

    with caplog.at_level(logging.INFO, logger="timothy.bot"):
        await __main__.check_backend(http)

    assert backend.called == ("GET", "/health")
    assert "backend reachable" in caplog.text


async def test_a_backend_that_is_not_healthy_stops_the_process(
    http: httpx.AsyncClient, backend: Backend
) -> None:
    backend.replies(503, {"detail": "not yet"})

    with pytest.raises(httpx.HTTPStatusError):
        await __main__.check_backend(http)


async def test_the_gateway_can_be_left_closed(
    http: httpx.AsyncClient,
    backend: Backend,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What CI runs: the image, the process and the backend connection are exercised,
    with a placeholder token and no application to log in to. The process stays up, so
    the timeout below is the assertion that it idled rather than exited."""
    monkeypatch.setattr(api, "create_client", lambda **_kwargs: http)
    backend.replies(200, {"status": "ok", "version": "0.1.0"})

    with caplog.at_level(logging.WARNING, logger="timothy.bot"), pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await __main__.run(Settings(gateway_enabled=False))

    assert "gateway disabled" in caplog.text


async def test_the_gateway_opens_with_the_configured_token(
    http: httpx.AsyncClient, backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[str] = []

    async def record(_self: TimothyBot, token: str) -> None:
        started.append(token)

    monkeypatch.setattr(api, "create_client", lambda **_kwargs: http)
    monkeypatch.setattr(TimothyBot, "start", record)
    backend.replies(200, {"status": "ok", "version": "0.1.0"})

    await __main__.run(Settings(discord_token="gateway-token", sync_commands=False))

    assert started == ["gateway-token"]


def test_main_configures_logging_before_it_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Uvicorn's counterpart of this was the reason nothing the backend logged reached
    its container's output. The bot has no framework doing it either."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("TIMOTHY_LOG_LEVEL", "debug")
    monkeypatch.setenv("TIMOTHY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(__main__.logs, "configure", lambda *_a, **kw: calls.append(kw))
    monkeypatch.setattr(__main__.asyncio, "run", lambda coroutine: coroutine.close())

    __main__.main()

    assert calls[0]["level"] == "debug"
    assert calls[0]["log_dir"] == tmp_path


def test_main_registers_its_credentials_for_redaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR 0014: the bot holds the token discord.py puts in an `Authorization` header
    and then quotes back in its own error messages. Nothing else in this process is in a
    position to keep it out of the file."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("TIMOTHY_DISCORD_TOKEN", "gateway-token-value")
    monkeypatch.setenv("TIMOTHY_INTERNAL_TOKEN", "internal-token-value")
    monkeypatch.setenv("TIMOTHY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(__main__.logs, "configure", lambda *_a, **kw: calls.append(kw))
    monkeypatch.setattr(__main__.asyncio, "run", lambda coroutine: coroutine.close())

    __main__.main()

    assert set(calls[0]["secrets"]) == {"gateway-token-value", "internal-token-value"}


def test_main_quiets_the_http_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The relay already logs a line per event, saying what the backend decided."""
    monkeypatch.setenv("TIMOTHY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(__main__.asyncio, "run", lambda coroutine: coroutine.close())

    __main__.main()

    assert logging.getLogger("httpx").level == logging.WARNING
