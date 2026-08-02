"""Fetching the guild list, and reading it back."""

import json
from datetime import UTC, datetime
from pathlib import Path

import dumps
import httpx
import pytest

from timothy_migration import guilds


def responder(pages: list[list[dict[str, str]]]) -> httpx.Client:
    """A client that answers `GET /users/@me/guilds` with `pages`, in order."""
    served = iter(pages)

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bot a-token"
        return httpx.Response(200, json=next(served, []))

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_it_fetches_one_page() -> None:
    with responder([[{"id": "2001", "name": "one"}, {"id": "2002", "name": "two"}]]) as http:
        snapshot = guilds.fetch("a-token", client=http)

    assert snapshot.guild_ids == {2001, 2002}


def test_it_follows_pagination_by_snowflake() -> None:
    """A truncated guild list reads as "these guilds left" and unsubscribes them, so the
    loop is written even though Timothy fits in one page today."""
    full = [{"id": str(3000 + n), "name": str(n)} for n in range(guilds.PAGE_SIZE)]
    with responder([full, [{"id": "9999", "name": "last"}]]) as http:
        snapshot = guilds.fetch("a-token", client=http)

    assert len(snapshot.guilds) == guilds.PAGE_SIZE + 1
    assert 9999 in snapshot.guild_ids


def test_a_refusal_from_discord_is_an_error() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="401: Unauthorized")

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(guilds.GuildFetchError, match="Discord answered 401"),
    ):
        guilds.fetch("a-token", client=http)


def test_an_unreachable_discord_is_an_error() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")  # noqa: EM101, TRY003 — httpx's own error, as httpx raises it

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(guilds.GuildFetchError, match="could not reach Discord"),
    ):
        guilds.fetch("a-token", client=http)


def test_an_answer_that_is_not_a_list_is_an_error() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "You are being rate limited."})

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(guilds.GuildFetchError, match="expected a list of guilds"),
    ):
        guilds.fetch("a-token", client=http)


def test_a_snapshot_round_trips(tmp_path: Path) -> None:
    written = guilds.Snapshot(
        fetched_at=datetime(2026, 8, 2, 9, 0, tzinfo=UTC),
        guilds=(guilds.GuildRecord(guild_id=2001, name="one"),),
    )
    path = tmp_path / "guilds.json"
    written.write(path)

    assert guilds.Snapshot.read(path) == written


def test_ids_are_written_as_strings(tmp_path: Path) -> None:
    """A snowflake past 2^53 loses precision as a JSON number, and the snapshot is a file
    people open."""
    path = tmp_path / "guilds.json"
    guilds.Snapshot(
        fetched_at=datetime(2026, 8, 2, tzinfo=UTC),
        guilds=(guilds.GuildRecord(guild_id=1234567890123456789, name="one"),),
    ).write(path)

    assert json.loads(path.read_text())["guilds"][0]["id"] == "1234567890123456789"


def test_an_empty_snapshot_is_refused(tmp_path: Path) -> None:
    """It would import cleanly and produce a database in which nothing is enforced
    anywhere — not a state worth being able to reach by accident."""
    path = tmp_path / "guilds.json"
    path.write_text(json.dumps({"version": 1, "fetched_at": "2026-08-02", "guilds": []}))

    with pytest.raises(guilds.GuildFetchError, match="lists no guilds"):
        guilds.Snapshot.read(path)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"version": 99, "guilds": [{"id": "1"}]}, "not a version 1 guild snapshot"),
        ({"version": 1, "guilds": ["nope"]}, "not a guild"),
        ({"version": 1, "guilds": [{"id": "not-a-snowflake"}]}, "not a snowflake"),
        ({"version": 1, "guilds": [{"name": "no id"}]}, "not a snowflake"),
    ],
)
def test_a_malformed_snapshot_is_refused(
    tmp_path: Path, document: dict[str, object], expected: str
) -> None:
    path = tmp_path / "guilds.json"
    path.write_text(json.dumps(document))

    with pytest.raises(guilds.GuildFetchError, match=expected):
        guilds.Snapshot.read(path)


def test_a_missing_snapshot_is_refused(tmp_path: Path) -> None:
    with pytest.raises(guilds.GuildFetchError, match="cannot read the guild snapshot"):
        guilds.Snapshot.read(tmp_path / "nowhere.json")


def test_the_fixture_snapshot_is_the_shape_the_tool_writes(tmp_path: Path) -> None:
    """The test helper and the real writer have to agree, or every other test in this
    directory is exercising a format nothing produces."""
    path = dumps.snapshot(tmp_path / "guilds.json", [2001, 2002])

    assert guilds.Snapshot.read(path).guild_ids == {2001, 2002}


def test_a_snapshot_with_no_readable_date_is_refused(tmp_path: Path) -> None:
    """`fetched_at` becomes `guilds.joined_at` for every guild the import writes, so an
    unreadable one would put a wrong date on every row rather than leaving one off."""
    path = tmp_path / "guilds.json"
    path.write_text(json.dumps({"version": 1, "guilds": [{"id": "2001"}]}))

    with pytest.raises(guilds.GuildFetchError, match="no readable fetched_at"):
        guilds.Snapshot.read(path)


def test_a_naive_date_is_read_as_utc(tmp_path: Path) -> None:
    """The column type rejects naive datetimes outright, so a hand-edited snapshot would
    otherwise fail at the insert rather than at the read."""
    path = tmp_path / "guilds.json"
    path.write_text(
        json.dumps(
            {"version": 1, "fetched_at": "2026-08-02T09:00:00", "guilds": [{"id": "2001"}]}
        )
    )

    assert guilds.Snapshot.read(path).fetched_at.tzinfo is UTC


def test_pagination_stops_on_an_empty_page() -> None:
    """A full last page is followed by an empty one, which is the other way the loop
    ends."""
    full = [{"id": str(3000 + n), "name": str(n)} for n in range(guilds.PAGE_SIZE)]
    with responder([full, []]) as http:
        snapshot = guilds.fetch("a-token", client=http)

    assert len(snapshot.guilds) == guilds.PAGE_SIZE


def test_fetch_opens_and_closes_its_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production path — the `client` argument exists for these tests. The close
    matters because `guilds fetch` is one command in a runbook, and a leaked connection
    is a process that does not exit."""
    opened = responder([[{"id": "2001", "name": "one"}]])
    monkeypatch.setattr(guilds.httpx, "Client", lambda **_: opened)

    snapshot = guilds.fetch("a-token")

    assert snapshot.guild_ids == {2001}
    assert opened.is_closed
