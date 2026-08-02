"""Every route the bot calls, checked against the API's own schema.

`GET /openapi.json` is the contract between the two containers, and this is the test that
makes it one. The bot cannot import the backend — that is the layering, and
`test_boundaries.py` enforces it — so nothing stops a path here drifting from a path
there except a check like this one. A route renamed in `apps/api` fails here rather than
in production, where it would surface as a 404 rendered into a red embed.

The paths are read out of `api.py` itself rather than listed again, because a second list
is a second thing to forget.
"""

import ast
from pathlib import Path

import pytest

from timothy_api.app import create_app
from timothy_api.settings import Settings
from timothy_bot import api as bot_api
from timothy_core.ports.fake import FakeDiscord

SOURCE = Path(bot_api.__file__)

HEALTH = ("GET", "/health")
"""Not called through `Api` — `__main__` waits on it before opening the gateway."""


def _literal(node: ast.expr) -> str | None:
    """The string a node is, if it is one. `_event` passes its path along in a variable,
    and that call site is covered by the literals in the two methods that use it."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def called_routes() -> set[tuple[str, str]]:
    """Every `(method, path template)` literal in the bot's API client."""
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "_request" and len(node.args) >= 2:
            method, template = _literal(node.args[0]), _literal(node.args[1])
            if method is not None and template is not None:
                found.add((method, template))
        elif node.func.attr == "_event" and node.args:
            path = _literal(node.args[0])
            if path is not None:
                found.add(("POST", path))
    return found


@pytest.fixture(scope="module")
def schema() -> dict[str, object]:
    """The real application's OpenAPI document."""
    app = create_app(
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            internal_token="contract-test",
            management_guild_id=1,
            workers_enabled=False,
        ),
        discord_port=FakeDiscord(),
    )
    return app.openapi()


def published_routes(schema: dict[str, object]) -> set[tuple[str, str]]:
    paths: dict[str, dict[str, object]] = schema["paths"]  # ty: ignore[invalid-assignment]
    return {
        (method.upper(), path) for path, operations in paths.items() for method in operations
    }


def test_the_client_calls_something() -> None:
    """A parser that quietly found nothing would make every check below vacuous."""
    assert len(called_routes()) >= 15


@pytest.mark.parametrize("route", [*sorted(called_routes()), HEALTH])
def test_the_backend_publishes_this_route(
    schema: dict[str, object], route: tuple[str, str]
) -> None:
    assert route in published_routes(schema)


def test_snowflakes_cross_the_wire_as_strings(schema: dict[str, object]) -> None:
    """Which is why the bot sends `str(user_id)` and never the integer: the same schema
    generates the web UI's client, and JavaScript's numbers stop being exact well below
    a Discord ID."""
    components: dict[str, dict[str, object]] = schema["components"]  # ty: ignore[invalid-assignment]
    event: dict[str, dict[str, dict[str, str]]] = components["schemas"]["GatewayEvent"]  # ty: ignore[invalid-assignment]

    assert event["properties"]["guild_id"]["type"] == "string"
    assert event["properties"]["user_id"]["type"] == "string"
