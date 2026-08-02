"""Building `mongodump` directories to import from.

Real BSON, written the way `mongodump` writes it — concatenated documents, no framing —
so the reader is exercised rather than stubbed. The whole point of taking the dump route
was that the input is a file; a test suite that handed the parser dictionaries would be
testing a different program.

Shared by plain import rather than through `conftest.py`, for the reason phase 4's
handoff records: two test directories both called `tests` cannot both resolve, so this is
a module on `pythonpath` and `conftest.py` holds fixtures alone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import bson

if TYPE_CHECKING:
    from pathlib import Path

EPOCH = datetime(2019, 3, 1, 12, 0, tzinfo=UTC)
"""A fixed date for every fixture document, so ordering in the tests is the ordering the
test wrote and not the ordering a clock happened to produce."""


def at(days: int = 0) -> datetime:
    """A timestamp `days` after the fixtures' epoch."""
    return datetime.fromtimestamp(EPOCH.timestamp() + days * 86400, tz=UTC)


def write_collection(root: Path, name: str, documents: list[dict[str, Any]]) -> Path:
    """Write one `<name>.bson` the way `mongodump` would."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.bson"
    path.write_bytes(b"".join(bson.encode(document) for document in documents))
    return path


def pool(name: str, description: str = "a pool", *, days: int = 0) -> dict[str, Any]:
    """A `banpools` document."""
    return {"pool_name": name, "pool_desc": description, "timestamp": at(days)}


def ban(
    user_id: int | str,
    pool_name: str,
    *,
    reason: str = "spam",
    creator_id: str = "0",
    days: int = 0,
) -> dict[str, Any]:
    """A `bans` document — a listing, in Timothy's language."""
    return {
        "user_id": str(user_id),
        "pool_name": pool_name,
        "reason": reason,
        "creator_id": creator_id,
        "timestamp": at(days),
    }


def subscription(
    guild_id: int | str,
    pool_name: str,
    level: str = "ban",
    *,
    creator_id: str = "0",
    days: int = 0,
) -> dict[str, Any]:
    """A `subscriptions` document."""
    return {
        "server_id": str(guild_id),
        "pool_name": pool_name,
        "subscription_level": level,
        "creator_id": creator_id,
        "timestamp": at(days),
    }


def exception(
    guild_id: int | str, user_id: int | str, *, creator_id: str = "0", days: int = 0
) -> dict[str, Any]:
    """An `exceptions` document."""
    return {
        "server_id": str(guild_id),
        "user_id": str(user_id),
        "creator_id": creator_id,
        "timestamp": at(days),
    }


def notification(
    guild_id: int | str, channel_id: int | str, *, author_id: str = "0", days: int = 0
) -> dict[str, Any]:
    """A `notifications` document."""
    return {
        "server_id": str(guild_id),
        "channel_id": str(channel_id),
        "author_id": author_id,
        "timestamp": at(days),
    }


def build(  # noqa: PLR0913 — one keyword per collection; the arity is the data's shape
    root: Path,
    *,
    banpools: list[dict[str, Any]] | None = None,
    bans: list[dict[str, Any]] | None = None,
    subscriptions: list[dict[str, Any]] | None = None,
    exceptions: list[dict[str, Any]] | None = None,
    notifications: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a whole dump. Collections left out are absent, as they would really be."""
    collections = {
        "banpools": banpools,
        "bans": bans,
        "subscriptions": subscriptions,
        "exceptions": exceptions,
        "notifications": notifications,
    }
    root.mkdir(parents=True, exist_ok=True)
    for name, documents in collections.items():
        if documents is not None:
            write_collection(root, name, documents)
    return root


def snapshot(path: Path, guild_ids: list[int], *, fetched: datetime | None = None) -> Path:
    """Write a guild snapshot in the form `timothy-migrate guilds` writes."""
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "fetched_at": (fetched or at(365)).isoformat(),
                "guilds": [
                    {"id": str(guild_id), "name": f"guild {guild_id}"} for guild_id in guild_ids
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
