"""The browser reporting its own crashes (ADR 0014).

The one route where a client decides what gets written to Timothy's disk, so most of
what is asserted here is about the limits rather than about the happy path.
"""

import logging

import httpx2
import pytest
from fastapi.testclient import TestClient

from timothy_api.routers import client_logs

from .conftest import GUILD_ADMIN, FakeOAuth, as_browser, headers, sign_in


@pytest.fixture(autouse=True)
def _fresh_budget() -> None:
    """A per-process limiter would otherwise carry one test's spend into the next."""
    client_logs._budget = client_logs.Budget()  # noqa: SLF001


def report(client: TestClient, **overrides: object) -> httpx2.Response:
    """One report, as the SPA sends it."""
    body = {"level": "error", "message": "TypeError: x is not a function", **overrides}
    return client.post("/client-logs", json=body, headers=as_browser("POST"))


def test_a_browser_error_reaches_the_log(
    client: TestClient, oauth: FakeOAuth, caplog: pytest.LogCaptureFixture
) -> None:
    sign_in(client, oauth)

    with caplog.at_level(logging.ERROR, logger="timothy.web"):
        response = report(client, url="https://timothy.example.com/pools", kind="render")

    assert response.status_code == 202
    (record,) = caplog.records
    assert "TypeError: x is not a function" in record.getMessage()
    assert record.__dict__["client_url"] == "https://timothy.example.com/pools"
    assert record.__dict__["actor"] == f"user:{GUILD_ADMIN}"


def test_the_stack_is_carried(
    client: TestClient, oauth: FakeOAuth, caplog: pytest.LogCaptureFixture
) -> None:
    """A minified stack is the whole reason this route exists."""
    sign_in(client, oauth)

    with caplog.at_level(logging.ERROR, logger="timothy.web"):
        report(client, stack="at r (index-abc123.js:1:4821)")

    (record,) = caplog.records
    assert record.__dict__["client_stack"] == "at r (index-abc123.js:1:4821)"


def test_nobody_signed_out_may_write_here(client: TestClient) -> None:
    """There is no anonymous write path to the log file."""
    assert client.post("/client-logs", json={"message": "hi"}).status_code == 401


def test_a_service_caller_may_report_too(client: TestClient) -> None:
    """The gate is the API's own, not a browser-only one — the bot's future crash
    reporting would come through here as well."""
    response = client.post(
        "/client-logs", json={"message": "boom"}, headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 202


def test_a_client_may_not_claim_critical(client: TestClient, oauth: FakeOAuth) -> None:
    """A report *about* a browser is not a log line from a trusted process."""
    sign_in(client, oauth)

    assert report(client, level="critical").status_code == 422


def test_an_enormous_stack_is_refused(client: TestClient, oauth: FakeOAuth) -> None:
    sign_in(client, oauth)

    response = report(client, stack="x" * (client_logs.MAX_STACK + 1))

    assert response.status_code == 422


def test_a_runaway_client_is_cut_off(
    client: TestClient, oauth: FakeOAuth, caplog: pytest.LogCaptureFixture
) -> None:
    """A component that throws on every frame is what this route is for, and also what
    would fill the disk while doing it."""
    sign_in(client, oauth)

    with caplog.at_level(logging.ERROR, logger="timothy.web"):
        statuses = [
            report(client).status_code for _ in range(client_logs.BUDGET_PER_MINUTE + 5)
        ]

    assert statuses.count(202) == client_logs.BUDGET_PER_MINUTE
    assert statuses.count(429) == 5
    assert len(caplog.records) == client_logs.BUDGET_PER_MINUTE


def test_the_budget_is_per_actor() -> None:
    """One noisy browser must not silence everybody else's reports."""
    budget = client_logs.Budget(limit=1)

    assert budget.allow("user:1")
    assert not budget.allow("user:1")
    assert budget.allow("user:2")


def test_a_cross_origin_report_is_refused(client: TestClient, oauth: FakeOAuth) -> None:
    """A POST from a browser is a state change like any other."""
    sign_in(client, oauth)

    response = client.post("/client-logs", json={"message": "boom"})

    assert response.status_code == 403
