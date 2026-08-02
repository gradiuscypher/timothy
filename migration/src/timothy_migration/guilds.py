"""Which guilds Timothy is actually in.

ADR 0002 turns `global` from a hardcoded short-circuit into an ordinary pool, which means
the import has to write a real `global` subscription row for every guild the bot is in
today — or those guilds silently stop enforcing the shared banlist on cutover morning.

The dump cannot answer that question. Mongo never had a guild collection: a guild
appeared in `subscriptions`, `exceptions` or `notifications` only once somebody
configured something there, and the guilds most exposed by this change are exactly the
ones that configured nothing and rode the implicit global. Discord is the only source
that knows.

So the guild list is fetched from Discord and **written to a file**. The fetch is the one
step that touches the network; the import then reads the file. That keeps the import
offline and repeatable — the same dump and the same snapshot produce the same database,
so a rehearsal is evidence about the real run — and it makes the guild list a reviewable
artefact rather than a transient API response nobody saw.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, cast

import httpx

if TYPE_CHECKING:
    from pathlib import Path

API_BASE: Final = "https://discord.com/api/v10"
PAGE_SIZE: Final = 200
"""The maximum `GET /users/@me/guilds` accepts. Timothy is in the low hundreds of guilds,
so this is one or two requests, but the loop is written anyway: the failure mode of
getting pagination wrong is a truncated guild list, which reads as "these guilds left"
and unsubscribes them."""

SNAPSHOT_VERSION: Final = 1


class GuildFetchError(Exception):
    """Discord would not say which guilds Timothy is in."""


@dataclass(frozen=True, slots=True)
class GuildRecord:
    """One guild Timothy is in.

    The name is carried purely so a human can read the snapshot and recognise the
    deployment. Nothing in the import uses it.
    """

    guild_id: int
    name: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The guild list as it was at a moment, and when that moment was."""

    fetched_at: datetime
    guilds: tuple[GuildRecord, ...]

    @property
    def guild_ids(self) -> frozenset[int]:
        """Just the IDs — what the import actually works from."""
        return frozenset(guild.guild_id for guild in self.guilds)

    def write(self, path: Path) -> None:
        """Save the snapshot where the import will read it."""
        path.write_text(
            json.dumps(
                {
                    "version": SNAPSHOT_VERSION,
                    "fetched_at": self.fetched_at.isoformat(),
                    "guilds": [
                        {"id": str(guild.guild_id), "name": guild.name} for guild in self.guilds
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @classmethod
    def read(cls, path: Path) -> Snapshot:
        """Load a snapshot written by :meth:`write`.

        Raises:
            GuildFetchError: the file is not a snapshot, or holds no guilds.
        """
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            msg = f"cannot read the guild snapshot at {path}: {error}"
            raise GuildFetchError(msg) from error

        if not isinstance(document, dict) or document.get("version") != SNAPSHOT_VERSION:
            msg = f"{path} is not a version {SNAPSHOT_VERSION} guild snapshot"
            raise GuildFetchError(msg)

        raw_guilds = document.get("guilds")
        if not isinstance(raw_guilds, list) or not raw_guilds:
            # An empty guild list would import cleanly and produce a database in which
            # nothing is enforced anywhere. That is not a state worth being able to reach
            # by accident.
            msg = (
                f"{path} lists no guilds; Timothy is in at least one, "
                f"or there is nothing here to migrate"
            )
            raise GuildFetchError(msg)

        return cls(
            guilds=tuple(_record(entry, path) for entry in raw_guilds),
            fetched_at=_fetched_at(document.get("fetched_at"), path),
        )


def _fetched_at(raw: object, path: Path) -> datetime:
    """The moment the snapshot was taken.

    Not decoration: it becomes `guilds.joined_at` for every guild the import writes, so a
    snapshot with an unreadable date would put a wrong date on every row rather than
    leaving one off.
    """
    try:
        fetched = datetime.fromisoformat(str(raw))
    except ValueError as error:
        msg = f"{path} has no readable fetched_at: {raw!r}"
        raise GuildFetchError(msg) from error
    return fetched if fetched.tzinfo else fetched.replace(tzinfo=UTC)


def _record(entry: object, path: Path) -> GuildRecord:
    """One entry of a snapshot's `guilds` list, checked rather than trusted.

    A hand-edited snapshot is a supported thing to do — trimming the list is how an
    operator rehearses against a subset — so every field is validated on the way in.
    """
    if not isinstance(entry, dict):
        msg = f"{path} holds an entry that is not a guild: {entry!r}"
        raise GuildFetchError(msg)

    guild = cast("dict[str, Any]", entry)
    raw_id = str(guild.get("id", ""))
    if not raw_id.isdigit():
        msg = f"{path} holds a guild whose id is not a snowflake: {raw_id!r}"
        raise GuildFetchError(msg)
    return GuildRecord(guild_id=int(raw_id), name=str(guild.get("name", "")))


def fetch(token: str, *, client: httpx.Client | None = None) -> Snapshot:
    """Ask Discord which guilds this bot token is in.

    Paginated with `after`, which is how Discord's guild listing works: each page returns
    guilds with IDs above the cursor, and the last page is a short one.

    Args:
        token: the bot token, without the `Bot ` prefix.
        client: an HTTP client to use instead of opening one, for tests.

    Raises:
        GuildFetchError: Discord refused, or answered with something that is not a list
            of guilds.
    """
    owned = client is None
    http = client or httpx.Client(timeout=30.0)
    try:
        return Snapshot(fetched_at=datetime.now(UTC), guilds=tuple(_pages(http, token)))
    finally:
        if owned:
            http.close()


def _pages(http: httpx.Client, token: str) -> list[GuildRecord]:
    records: list[GuildRecord] = []
    after = 0
    while True:
        page = _page(http, token, after=after)
        if not page:
            return records
        records.extend(page)
        # `after` is a snowflake cursor, so it has to advance past the largest ID seen
        # rather than by a count. Discord returns the page ascending, but not relying on
        # that costs one `max()`.
        after = max(record.guild_id for record in page)
        if len(page) < PAGE_SIZE:
            return records


def _page(http: httpx.Client, token: str, *, after: int) -> list[GuildRecord]:
    try:
        response = http.get(
            f"{API_BASE}/users/@me/guilds",
            headers={"Authorization": f"Bot {token}"},
            params={"limit": PAGE_SIZE, "after": after},
        )
    except httpx.HTTPError as error:
        msg = f"could not reach Discord: {error}"
        raise GuildFetchError(msg) from error

    if response.status_code != httpx.codes.OK:
        msg = f"Discord answered {response.status_code}: {response.text[:200]}"
        raise GuildFetchError(msg)

    payload: Any = response.json()
    if not isinstance(payload, list):
        msg = f"Discord answered with {type(payload).__name__}, expected a list of guilds"
        raise GuildFetchError(msg)

    return [
        GuildRecord(guild_id=int(str(entry["id"])), name=str(entry.get("name", "")))
        for entry in payload
    ]
