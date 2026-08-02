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


def test_the_bot_declares_only_what_it_needs() -> None:
    """discord.py to hold the gateway, httpx to reach the backend, pydantic-settings to
    read the environment. Nothing that would let domain logic in."""
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    declared = {
        requirement.split(">")[0].split("[")[0].strip()
        for requirement in manifest["project"]["dependencies"]
    }

    assert declared == {"discord-py", "httpx", "pydantic-settings"}
