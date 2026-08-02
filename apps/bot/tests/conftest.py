"""Fixtures for the bot. The stand-ins they build are in `support.py`."""

import os

import httpx
import pytest
from support import Backend, FakeInteraction

from timothy_bot.api import SYSTEM, Api


@pytest.fixture(autouse=True)
def _no_ambient_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the developer's own `TIMOTHY_*` from every test.

    `Settings` reads the environment, so a shell with a real management guild or a real
    token in it would otherwise change what the tests are testing.
    """
    for name in list(os.environ):
        if name.startswith("TIMOTHY_"):
            monkeypatch.delenv(name)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def backend() -> Backend:
    return Backend()


@pytest.fixture
def http(backend: Backend) -> httpx.AsyncClient:
    """An HTTP client wired to the fake backend, carrying the internal token."""
    return httpx.AsyncClient(
        base_url="http://backend:8000",
        transport=httpx.MockTransport(backend.handle),
        headers={"Authorization": "Bearer internal-token-for-tests"},
    )


@pytest.fixture
def api(http: httpx.AsyncClient) -> Api:
    """The backend as Timothy itself sees it — what the event relay uses."""
    return Api(http, actor=SYSTEM)


@pytest.fixture
def interaction(api: Api) -> FakeInteraction:
    """A moderator, in a guild, invoking something."""
    return FakeInteraction(api)
