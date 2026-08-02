"""The door: the service token, and the actor assertion behind it."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from timothy_api.app import create_app
from timothy_api.settings import Settings
from timothy_core.ports.fake import FakeDiscord

from .conftest import POOL_ADMIN, headers


def test_a_call_without_the_token_is_refused(client: TestClient) -> None:
    response = client.get("/pools", headers=headers(token=None))

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_a_call_with_the_wrong_token_is_refused(client: TestClient) -> None:
    response = client.get("/pools", headers=headers(token="not-the-token"))

    assert response.status_code == 401


def test_an_unconfigured_token_refuses_everything(
    settings: Settings, discord: FakeDiscord
) -> None:
    """A missing environment variable must not read as "no token needed". That is the
    failure mode the token exists to prevent."""
    open_settings = settings.model_copy(update={"internal_token": SecretStr("")})

    with TestClient(create_app(open_settings, discord_port=discord)) as client:
        assert client.get("/pools", headers=headers()).status_code == 401


def test_the_actor_header_is_required(client: TestClient) -> None:
    """Silence must not mean `system`: Timothy's own operations are the ones with no
    Discord permission behind them to check."""
    response = client.get("/pools", headers=headers(actor=None))

    assert response.status_code == 400


def test_a_malformed_actor_is_a_bad_request(client: TestClient) -> None:
    response = client.get("/pools", headers=headers("nonsense"))

    assert response.status_code == 400
    assert "X-Timothy-Actor" in response.json()["detail"]


def test_a_well_formed_actor_is_accepted(registered: TestClient) -> None:
    response = registered.get("/pools", headers=headers(POOL_ADMIN))

    assert response.status_code == 200


def test_the_token_alone_grants_nothing(registered: TestClient) -> None:
    """Holding the token authenticates the caller, never the actor: what the named user
    may do is still resolved against Discord."""
    response = registered.post(
        "/pools", json={"name": "spam"}, headers=headers(999_000_000_000_000_001)
    )

    assert response.status_code == 403
