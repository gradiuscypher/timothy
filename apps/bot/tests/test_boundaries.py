"""PLAN.md's Layout, enforced rather than remembered.

The bot deliberately does not depend on `core`. It relays events and renders responses;
it has no domain logic to share, and giving it any would put a second copy of the rules
where the backend's copy is the only one authorization is enforced against (ADR 0003).

Nor does it depend on the API package. The two speak over HTTP, and `test_contract.py` is
how that stays honest — importing the backend to borrow a path would make the coupling
invisible and the containers inseparable.
"""

import ast
import tomllib
from pathlib import Path

SOURCE = Path(__file__).parent.parent / "src" / "timothy_bot"
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"
LOGS_PYPROJECT = Path(__file__).parents[3] / "packages" / "logs" / "pyproject.toml"

FORBIDDEN = {"timothy_core", "timothy_api"}


def _imported_roots(module: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_bot_imports_neither_the_domain_nor_the_backend() -> None:
    offenders = sorted(
        f"{module.relative_to(SOURCE).as_posix()}: "
        f"{sorted(FORBIDDEN & _imported_roots(module))}"
        for module in SOURCE.rglob("*.py")
        if FORBIDDEN & _imported_roots(module)
    )

    assert offenders == []


def _declared(manifest_path: Path) -> set[str]:
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        requirement.split(">")[0].split("[")[0].strip()
        for requirement in manifest["project"]["dependencies"]
    }


def test_the_bot_declares_only_what_it_needs() -> None:
    """discord.py to hold the gateway, httpx to reach the backend, pydantic-settings to
    read the environment, timothy-logs to write a log file. Nothing that would let domain
    logic in."""
    assert _declared(PYPROJECT) == {
        "discord-py",
        "httpx",
        "pydantic-settings",
        "timothy-logs",
    }


def test_the_logging_package_stays_a_leaf() -> None:
    """The one shared package the bot is allowed, and the reason it is allowed.

    `timothy-logs` is a file handler and a redactor with no dependencies of its own, so
    depending on it pulls in nothing (ADR 0014). The moment it grows one, this test is
    the thing that says so — otherwise it becomes the route by which the domain, or
    SQLAlchemy, arrives in the bot's image after all.
    """
    assert _declared(LOGS_PYPROJECT) == set()
