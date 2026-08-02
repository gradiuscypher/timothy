"""Parsing the old collections, and refusing to guess."""

from datetime import UTC, datetime
from pathlib import Path

import dumps
import pytest

from timothy_core.actors import Actor
from timothy_core.enums import SubscriptionLevel
from timothy_migration import records
from timothy_migration.dump import Dump


def read(root: Path) -> records.Source:
    return records.read(Dump(root))


def test_it_parses_every_collection(source: records.Source) -> None:
    assert source.counts() == {
        "banpools": 2,
        "bans": 3,
        "subscriptions": 2,
        "exceptions": 1,
        "notifications": 1,
    }
    assert source.rejected == []


def test_the_old_magic_creator_becomes_the_system_actor(source: records.Source) -> None:
    """ADR-adjacent: the old bot wrote `"0"` for its own actions and for everything the
    previous migration imported, indistinguishable from a real user."""
    assert all(listing.created_by.is_system for listing in source.listings)


def test_a_real_creator_stays_a_user(tmp_path: Path) -> None:
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        bans=[dumps.ban(1001, "global", creator_id="9001")],
    )

    assert read(root).listings[0].created_by == Actor.user(9001)


def test_an_empty_pool_description_becomes_null(tmp_path: Path) -> None:
    """Mongo's field was not nullable, so "no description" was spelled `""`."""
    root = dumps.build(tmp_path / "dump", banpools=[dumps.pool("global", "")])

    assert read(root).pools[0].description is None


def test_timestamps_come_back_aware(source: records.Source) -> None:
    """The column type rejects naive datetimes rather than guessing their offset."""
    assert source.pools[0].created_at.tzinfo is not None


def test_a_document_with_no_timestamp_is_kept(tmp_path: Path) -> None:
    """The date is decoration on a row whose value is the relationship it records."""
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        bans=[{"user_id": "1001", "pool_name": "global", "reason": "x", "creator_id": "0"}],
    )

    source = read(root)

    assert source.rejected == []
    assert source.listings[0].created_at == datetime.fromtimestamp(0, tz=UTC)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"user_id": "not-a-number", "pool_name": "global"}, "not a Discord ID"),
        ({"user_id": "0", "pool_name": "global"}, "is 0"),
        ({"user_id": "1001", "pool_name": ""}, "pool_name is empty"),
        ({"user_id": "1001"}, "pool_name is NoneType"),
    ],
)
def test_unusable_listings_are_quarantined_not_dropped(
    tmp_path: Path, document: dict[str, object], expected: str
) -> None:
    """Every rejection keeps the document, so an operator can go and look at it."""
    root = dumps.build(tmp_path / "dump", banpools=[dumps.pool("global")], bans=[document])

    source = read(root)

    assert source.listings == []
    assert len(source.rejected) == 1
    assert expected in source.rejected[0].reason
    assert source.rejected[0].document == document


def test_an_unknown_subscription_level_is_refused(tmp_path: Path) -> None:
    """Defaulting to `ban` would ban people on the strength of a typo; defaulting to
    `warn` would quietly stop enforcing a pool a guild believes it enforces."""
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("raiders")],
        subscriptions=[dumps.subscription(2001, "raiders", "kick")],
    )

    source = read(root)

    assert source.subscriptions == []
    assert "neither ban nor warn" in source.rejected[0].reason


def test_the_level_is_read_case_insensitively(tmp_path: Path) -> None:
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("raiders")],
        subscriptions=[dumps.subscription(2001, "raiders", "BAN")],
    )

    assert read(root).subscriptions[0].level is SubscriptionLevel.BAN


def test_a_listing_with_no_reason_is_kept(tmp_path: Path) -> None:
    """An empty reason is worse copy than "no reason recorded", and still the truth."""
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        bans=[dumps.ban(1001, "global", reason="")],
    )

    source = read(root)

    assert source.rejected == []
    assert source.listings[0].reason == ""


def test_a_pool_name_too_long_for_the_column_is_refused(tmp_path: Path) -> None:
    """SQLite would not enforce `String(64)`, which is exactly why it is checked here."""
    root = dumps.build(tmp_path / "dump", banpools=[dumps.pool("x" * 65)])

    assert "longer than 64" in read(root).rejected[0].reason


def test_an_id_stored_as_a_number_is_accepted(tmp_path: Path) -> None:
    """Every ID was meant to be a string, and nine years of writes did not all agree."""
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        bans=[{**dumps.ban(1001, "global"), "user_id": 1001, "creator_id": 9001}],
    )

    source = read(root)

    assert source.listings[0].user_id == 1001
    assert source.listings[0].created_by == Actor.user(9001)


def test_notifications_read_their_author_from_the_field_they_used(
    source: records.Source,
) -> None:
    """`notifications` spelled the creator `author_id`; everything else used
    `creator_id`."""
    assert source.notification_channels[0].guild_id == 2002
    assert source.notification_channels[0].channel_id == 3001


def test_an_id_that_is_neither_a_string_nor_a_number_is_refused(tmp_path: Path) -> None:
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        bans=[{**dumps.ban(1001, "global"), "user_id": ["1001"]}],
    )

    assert "expected a Discord ID" in read(root).rejected[0].reason


@pytest.mark.parametrize(
    ("creator", "expected"),
    [
        ({"oid": 1}, "expected a Discord ID"),
        ("not-a-number", "not a Discord ID"),
    ],
)
def test_an_unreadable_creator_is_refused(
    tmp_path: Path, creator: object, expected: str
) -> None:
    """The actor is not decoration: it is who a moderator sees when they ask why someone
    is on a pool, and a wrong answer there is worse than a rejected row."""
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        bans=[{**dumps.ban(1001, "global"), "creator_id": creator}],
    )

    source = read(root)

    assert source.listings == []
    assert expected in source.rejected[0].reason


def test_a_missing_creator_reads_as_the_system(tmp_path: Path) -> None:
    """Rows that predate the field. `system` is the honest reading of "nobody alive knows
    who asked for this"; a snowflake would be a guess attributed to a real person."""
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        bans=[{"user_id": "1001", "pool_name": "global", "reason": "x"}],
    )

    assert read(root).listings[0].created_by == Actor.system()


def test_an_unusable_exception_is_quarantined(tmp_path: Path) -> None:
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        exceptions=[dumps.exception("not-a-guild", 1001)],
    )

    source = read(root)

    assert source.exceptions == []
    assert source.rejected[0].collection == "exceptions"


def test_an_unusable_notification_channel_is_quarantined(tmp_path: Path) -> None:
    root = dumps.build(
        tmp_path / "dump",
        banpools=[dumps.pool("global")],
        notifications=[dumps.notification(2001, "0")],
    )

    source = read(root)

    assert source.notification_channels == []
    assert "channel_id is 0" in source.rejected[0].reason
