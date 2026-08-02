"""What the migration is allowed to depend on, enforced rather than remembered."""

import ast
import tomllib
from pathlib import Path

SOURCE = Path(__file__).parent.parent / "src" / "timothy_migration"
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _imported_roots(module: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_it_never_imports_the_backend_or_the_bot() -> None:
    """It shares `core` — the schema and the decision logic are the two things it has to
    agree with exactly — and nothing else. Reaching into `timothy_api` would let the
    import write rows through the enforcement path, which is how a migration ends up
    banning people."""
    forbidden = {"timothy_api", "timothy_bot"}
    offenders = sorted(
        f"{module.relative_to(SOURCE).as_posix()}: "
        f"{sorted(forbidden & _imported_roots(module))}"
        for module in SOURCE.rglob("*.py")
        if forbidden & _imported_roots(module)
    )

    assert offenders == []


def test_it_never_opens_a_mongo_connection() -> None:
    """`pymongo` is a dependency for `bson` alone. The importer reads a `mongodump`
    directory, which is what makes the import repeatable and a rehearsal evidence about
    the real run."""
    offenders = sorted(
        module.relative_to(SOURCE).as_posix()
        for module in SOURCE.rglob("*.py")
        if "pymongo" in _imported_roots(module)
    )

    assert offenders == []


def test_it_declares_only_what_it_needs() -> None:
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    declared = {
        requirement.split(">")[0].split("[")[0].strip()
        for requirement in manifest["project"]["dependencies"]
    }

    assert declared == {"timothy-core", "pymongo", "httpx"}


def test_only_the_loader_writes() -> None:
    """`verify` and `diff` read and report; nothing else in the package may insert,
    update or delete. An operator has to be able to re-run a check as many times as it
    takes without wondering what it changed."""
    writers = {"insert", "update", "delete"}
    offenders = sorted(
        module.relative_to(SOURCE).as_posix()
        for module in SOURCE.rglob("*.py")
        if module.name != "load.py" and writers & _imported_roots_of_sqlalchemy(module)
    )

    assert offenders == []


def _imported_roots_of_sqlalchemy(module: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy":
            names.update(alias.name for alias in node.names)
    return names
