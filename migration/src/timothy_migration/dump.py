"""Reading a `mongodump` directory.

`mongodump` writes one `<collection>.bson` file per collection, each a bare concatenation
of BSON documents with no container framing — the length prefix of each document is what
says where the next one starts. `bson.decode_file_iter` knows that, so this module is
mostly about which files to look for and what to say when one is missing.

The importer never opens a Mongo connection. That is the point of taking a dump: the
input is a file on disk, so the import is repeatable, and a rehearsal is evidence about
the real run rather than a separate event with its own timing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import bson

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

BANPOOLS: Final = "banpools"
BANS: Final = "bans"
SUBSCRIPTIONS: Final = "subscriptions"
EXCEPTIONS: Final = "exceptions"
NOTIFICATIONS: Final = "notifications"

IMPORTED: Final = (BANPOOLS, BANS, SUBSCRIPTIONS, EXCEPTIONS, NOTIFICATIONS)
"""The collections that become rows. Order is the order they are reported in."""

DEAD: Final = ("adminroles", "serverconfig")
"""Collections with no live callers, dropped rather than imported (PLAN.md).

Their slash commands are in the old repository's `json_commands/archive/`. Named here
rather than merely omitted so that finding them in a dump is a recognised outcome and not
a surprise.
"""


class DumpError(Exception):
    """The dump is not one, or is missing something the import needs."""


class Dump:
    """A `mongodump` directory, read lazily.

    A dump of a database is a directory of `<collection>.bson` files; `mongodump` without
    `--db` writes one directory per database inside the top level. Both layouts are
    accepted, because which one an operator produces depends on flags they will not
    remember passing.
    """

    def __init__(self, root: Path) -> None:
        """Locate the collection files under `root`.

        Raises:
            DumpError: `root` is not a directory, or holds no collection this import
                knows about.
        """
        if not root.is_dir():
            msg = f"not a directory: {root}"
            raise DumpError(msg)

        self.root = _collections_dir(root)
        self.present = tuple(
            name for name in IMPORTED if (self.root / f"{name}.bson").is_file()
        )
        if not self.present:
            msg = (
                f"no collections found under {root} — expected files named "
                f"{', '.join(f'{name}.bson' for name in IMPORTED)}"
            )
            raise DumpError(msg)

    @property
    def missing(self) -> tuple[str, ...]:
        """Collections this import reads that the dump does not contain.

        Not an error. A deployment that has never had a warn-level subscription has no
        `subscriptions.bson`, and an empty collection and an absent one mean the same
        thing. It is reported, because the other reason a file is absent is a partial
        dump.
        """
        return tuple(name for name in IMPORTED if name not in self.present)

    @property
    def dead_present(self) -> tuple[str, ...]:
        """Dead collections found in the dump and deliberately not imported."""
        return tuple(name for name in DEAD if (self.root / f"{name}.bson").is_file())

    def documents(self, collection: str) -> Iterator[dict[str, Any]]:
        """Every document in `collection`, in the order `mongodump` wrote them.

        Yields nothing for a collection the dump does not contain.

        Raises:
            DumpError: the file exists but is not decodable BSON.
        """
        path = self.root / f"{collection}.bson"
        if not path.is_file():
            return

        with path.open("rb") as handle:
            try:
                yield from bson.decode_file_iter(handle)
            except bson.InvalidBSON as error:
                msg = f"{path} is not a readable BSON dump: {error}"
                raise DumpError(msg) from error


def _collections_dir(root: Path) -> Path:
    """Resolve `root` to the directory the `.bson` files are actually in.

    `mongodump --db banpool -o dump/` writes `dump/banpool/*.bson`; `mongodump --db
    banpool --out -` and friends write them flat. If the collections are not directly
    under `root`, look one level down for a directory that has them.
    """
    if any((root / f"{name}.bson").is_file() for name in IMPORTED):
        return root

    candidates = sorted(
        child
        for child in root.iterdir()
        if child.is_dir() and any((child / f"{name}.bson").is_file() for name in IMPORTED)
    )
    if len(candidates) > 1:
        names = ", ".join(child.name for child in candidates)
        msg = f"{root} holds more than one database ({names}); point at the one to import"
        raise DumpError(msg)
    return candidates[0] if candidates else root
