from fastapi.testclient import TestClient

from timothy_api.schemas import GuildRead

from .conftest import GUILD, GUILD_ADMIN, headers


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_needs_no_credentials(client: TestClient) -> None:
    """The compose healthcheck has none, and neither will a load balancer."""
    assert "Authorization" not in client.headers
    assert client.get("/health").status_code == 200


def test_openapi_schema_is_generated(client: TestClient) -> None:
    """The web client is generated from this schema, so it has to exist."""
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Timothy"


def test_snowflakes_are_strings_in_the_schema() -> None:
    """A 64-bit ID parsed as a JSON number is a different ID by the time it reaches the
    browser, so the generated client has to be told they are strings."""
    assert GuildRead.model_json_schema()["properties"]["guild_id"]["type"] == "string"


def test_a_snowflake_serialises_as_a_string(registered: TestClient) -> None:
    response = registered.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN))

    assert response.status_code == 200
    assert response.json()["guild_id"] == str(GUILD)
