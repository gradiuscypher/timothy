"""ADR 0007's central consequence, enforced rather than remembered."""

import ast
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SOURCE = Path(__file__).parent.parent / "src" / "timothy_core"


def _imported_roots(module: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_domain_never_imports_discord_py() -> None:
    """The whole point of the port. `timothy_core.ports.discord` is ours; `discord` is not."""
    offenders = sorted(
        module.relative_to(SOURCE).as_posix()
        for module in SOURCE.rglob("*.py")
        if "discord" in _imported_roots(module)
    )

    assert offenders == []


@pytest.mark.anyio
async def test_the_pragmas_reach_every_connection(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Foreign keys are off by default in SQLite, and WAL is what lets readers read
    while the sole writer writes."""
    async with sessions() as session:
        foreign_keys = await session.execute(text("PRAGMA foreign_keys"))
        journal_mode = await session.execute(text("PRAGMA journal_mode"))

        assert foreign_keys.scalar_one() == 1
        assert journal_mode.scalar_one() == "wal"
