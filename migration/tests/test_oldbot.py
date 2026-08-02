"""The old bot's rule, transcribed rather than improved.

The reference these tests hold it to is `db_wrapper/src/mongo.rs` in `banpool-tim-gcp` —
`is_user_banned_on_guild`, `is_guild_subscribed`, `is_user_exception` — and `bot.rs`,
which is the only thing that called them in production.
"""

from pathlib import Path

import dumps

from timothy_migration import records
from timothy_migration.dump import Dump
from timothy_migration.oldbot import OldBot


def old(tmp_path: Path, **collections: list[dict[str, object]]) -> OldBot:
    return OldBot.from_source(
        records.read(Dump(dumps.build(tmp_path / "dump", **collections)))  # type: ignore[arg-type]
    )


def test_global_is_subscribed_by_every_guild_without_a_row(tmp_path: Path) -> None:
    """`is_guild_subscribed` returned true for the name `global` without looking
    anything up. No row ever existed, and no guild could leave."""
    bot = old(tmp_path, banpools=[dumps.pool("global")], bans=[dumps.ban(1001, "global")])

    assert bot.subscribes(9999, "global")
    assert bot.would_ban(guild_id=9999, user_id=1001)


def test_a_pool_that_is_not_global_needs_a_subscription(tmp_path: Path) -> None:
    bot = old(
        tmp_path,
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        bans=[dumps.ban(1001, "raiders")],
        subscriptions=[dumps.subscription(2001, "raiders")],
    )

    assert bot.would_ban(guild_id=2001, user_id=1001)
    assert not bot.would_ban(guild_id=2002, user_id=1001)


def test_the_level_was_never_read_by_the_live_check(tmp_path: Path) -> None:
    """The behaviour change `warn` represents. `is_user_banned_on_guild` asked whether the
    guild was subscribed and banned if it was; only the offline `tools.rs` sync, run by
    hand, ever looked at `subscription_level`."""
    bot = old(
        tmp_path,
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        bans=[dumps.ban(1001, "raiders")],
        subscriptions=[dumps.subscription(2001, "raiders", "warn")],
    )

    assert bot.would_ban(guild_id=2001, user_id=1001)


def test_an_exception_suppresses_the_ban(tmp_path: Path) -> None:
    bot = old(
        tmp_path,
        banpools=[dumps.pool("global")],
        bans=[dumps.ban(1001, "global")],
        exceptions=[dumps.exception(2001, 1001)],
    )

    assert not bot.would_ban(guild_id=2001, user_id=1001)
    assert bot.would_ban(guild_id=2002, user_id=1001)


def test_exceptions_are_guild_wide(tmp_path: Path) -> None:
    """Never scoped to one pool, then or now (ADR 0006)."""
    bot = old(
        tmp_path,
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        bans=[dumps.ban(1001, "global"), dumps.ban(1001, "raiders")],
        subscriptions=[dumps.subscription(2001, "raiders")],
        exceptions=[dumps.exception(2001, 1001)],
    )

    assert bot.justifying_pools(guild_id=2001, user_id=1001) == frozenset()


def test_a_listing_in_a_deleted_pool_still_counted(tmp_path: Path) -> None:
    """`get_user_bans` queried `bans` by user and never joined to `banpools`, so a ban in
    a pool `delete_pool` had removed still matched — and `is_guild_subscribed` matched
    the dead name too."""
    bot = old(
        tmp_path,
        banpools=[dumps.pool("global")],
        bans=[dumps.ban(1001, "gone-in-2021")],
        subscriptions=[dumps.subscription(2001, "gone-in-2021")],
    )

    assert bot.would_ban(guild_id=2001, user_id=1001)


def test_it_names_every_justifying_pool(tmp_path: Path) -> None:
    """The old function returned whichever reason Mongo happened to hand back first, so
    the check compares the decision and not the attribution."""
    bot = old(
        tmp_path,
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        bans=[dumps.ban(1001, "global"), dumps.ban(1001, "raiders")],
        subscriptions=[dumps.subscription(2001, "raiders")],
    )

    assert bot.justifying_pools(guild_id=2001, user_id=1001) == {"global", "raiders"}


def test_enforced_pairs_covers_every_guild_and_listed_user(tmp_path: Path) -> None:
    bot = old(
        tmp_path,
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        bans=[dumps.ban(1001, "global"), dumps.ban(1002, "raiders")],
        subscriptions=[dumps.subscription(2001, "raiders")],
    )

    assert bot.enforced_pairs([2001, 2002]) == {
        (2001, 1001),
        (2001, 1002),
        (2002, 1001),
    }
