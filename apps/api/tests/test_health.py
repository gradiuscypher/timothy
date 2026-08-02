from fastapi.testclient import TestClient

from timothy_api.app import create_app


def test_health_reports_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_schema_is_generated() -> None:
    """The web client is generated from this schema, so it has to exist."""
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Timothy"
