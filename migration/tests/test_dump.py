"""Reading a `mongodump` directory."""

from pathlib import Path

import dumps
import pytest

from timothy_migration.dump import Dump, DumpError


def test_it_reads_documents_back_in_order(tmp_path: Path) -> None:
    root = dumps.build(
        tmp_path / "dump", banpools=[dumps.pool("global"), dumps.pool("raiders")]
    )

    names = [document["pool_name"] for document in Dump(root).documents("banpools")]

    assert names == ["global", "raiders"]


def test_a_collection_the_dump_lacks_yields_nothing(tmp_path: Path) -> None:
    """Absent and empty mean the same thing, and neither is an error."""
    root = dumps.build(tmp_path / "dump", banpools=[dumps.pool("global")])

    assert list(Dump(root).documents("subscriptions")) == []
    assert Dump(root).missing == ("bans", "subscriptions", "exceptions", "notifications")


def test_it_finds_the_collections_one_level_down(tmp_path: Path) -> None:
    """`mongodump -o dump/` writes `dump/<database>/*.bson`, which is the common case."""
    dumps.build(tmp_path / "dump" / "banpool", banpools=[dumps.pool("global")])

    assert Dump(tmp_path / "dump").present == ("banpools",)


def test_two_databases_in_one_directory_are_refused(tmp_path: Path) -> None:
    """Guessing which one to import is not a decision a migration gets to make."""
    dumps.build(tmp_path / "dump" / "banpool", banpools=[dumps.pool("global")])
    dumps.build(tmp_path / "dump" / "banpool_staging", banpools=[dumps.pool("global")])

    with pytest.raises(DumpError, match="more than one database"):
        Dump(tmp_path / "dump")


def test_a_directory_with_no_collections_is_refused(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()

    with pytest.raises(DumpError, match="no collections found"):
        Dump(tmp_path / "empty")


def test_a_path_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    file = tmp_path / "dump.bson"
    file.write_bytes(b"")

    with pytest.raises(DumpError, match="not a directory"):
        Dump(file)


def test_a_file_that_is_not_bson_is_refused(tmp_path: Path) -> None:
    root = dumps.build(tmp_path / "dump", banpools=[dumps.pool("global")])
    (root / "bans.bson").write_bytes(b"this is not bson at all")

    with pytest.raises(DumpError, match="not a readable BSON dump"):
        list(Dump(root).documents("bans"))


def test_the_dead_collections_are_recognised_not_ignored(tmp_path: Path) -> None:
    """`adminroles` and `serverconfig` have no live callers (PLAN.md). Finding them is a
    recognised outcome, so the report can say they were skipped on purpose."""
    root = dumps.build(tmp_path / "dump", banpools=[dumps.pool("global")])
    dumps.write_collection(root, "adminroles", [{"server_id": "1", "role_id": "2"}])

    assert Dump(root).dead_present == ("adminroles",)
