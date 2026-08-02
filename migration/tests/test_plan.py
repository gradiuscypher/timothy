"""The transform: surrogate keys, duplicates, orphans, departed guilds, and `global`."""

from pathlib import Path

import dumps
import pytest

from timothy_core.actors import Actor
from timothy_core.enums import SubscriptionLevel
from timothy_migration import guilds, plan, records
from timothy_migration.dump import Dump


def planned(
    tmp_path: Path,
    guild_ids: list[int],
    *,
    global_pool: str = "global",
    **collections: list[dict[str, object]],
) -> plan.ImportPlan:
    """Build a plan from a one-off dump and guild list."""
    source = records.read(Dump(dumps.build(tmp_path / "dump", **collections)))  # type: ignore[arg-type]
    snapshot = guilds.Snapshot.read(dumps.snapshot(tmp_path / "guilds.json", guild_ids))
    return plan.build(source, snapshot, global_pool=global_pool)


# -- surrogate keys ----------------------------------------------------------


def test_pools_get_surrogate_keys_in_name_order(import_plan: plan.ImportPlan) -> None:
    """Arbitrary but reproducible: the same dump twice gives the same database twice."""
    assert [(pool.id, pool.name) for pool in import_plan.pools] == [
        (1, "global"),
        (2, "raiders"),
    ]


def test_listings_and_subscriptions_are_rewritten_to_pool_ids(
    import_plan: plan.ImportPlan,
) -> None:
    ids = import_plan.pool_ids_by_name()

    assert {(listing.user_id, listing.pool_id) for listing in import_plan.listings} == {
        (1001, ids["global"]),
        (1001, ids["raiders"]),
        (1002, ids["raiders"]),
    }
    assert (2001, ids["raiders"]) in {
        (subscription.guild_id, subscription.pool_id)
        for subscription in import_plan.subscriptions
    }


def test_pools_are_attributed_to_the_system(import_plan: plan.ImportPlan) -> None:
    """Mongo's `BanPool` recorded no author, and a snowflake here would be a guess
    attributed to a real person."""
    assert all(pool.created_by == Actor.system() for pool in import_plan.pools)


# -- global ------------------------------------------------------------------


def test_every_guild_gets_a_real_global_subscription(
    import_plan: plan.ImportPlan,
) -> None:
    """ADR 0002 as rows. The guild that configured nothing is the one this is for."""
    global_id = import_plan.pool_ids_by_name()["global"]
    subscribed = {
        subscription.guild_id
        for subscription in import_plan.subscriptions
        if subscription.pool_id == global_id
    }

    assert subscribed == {2001, 2002, 2003}


def test_the_materialised_global_is_held_at_ban(import_plan: plan.ImportPlan) -> None:
    """The old short-circuit banned, and did not consult a level to do it."""
    global_id = import_plan.pool_ids_by_name()["global"]

    assert all(
        subscription.level is SubscriptionLevel.BAN
        for subscription in import_plan.subscriptions
        if subscription.pool_id == global_id
    )


def test_an_existing_global_subscription_is_left_alone(tmp_path: Path) -> None:
    """A guild that deliberately set `global` to warn keeps it."""
    result = planned(
        tmp_path,
        [2001],
        banpools=[dumps.pool("global")],
        subscriptions=[dumps.subscription(2001, "global", "warn")],
    )

    assert [subscription.level for subscription in result.subscriptions] == [
        SubscriptionLevel.WARN
    ]


def test_materialising_is_reported(import_plan: plan.ImportPlan) -> None:
    grouped = import_plan.anomalies_by_kind()

    assert plan.Anomaly.GLOBAL_MATERIALISED in grouped
    assert "3 guilds" in grouped[plan.Anomaly.GLOBAL_MATERIALISED][0]


def test_a_missing_global_pool_stops_the_import(tmp_path: Path) -> None:
    """The wrong dump, or the wrong pool name — and importing anyway would unsubscribe
    every guild from the shared banlist without saying so."""
    with pytest.raises(plan.PlanError, match="no pool named 'global'"):
        planned(tmp_path, [2001], banpools=[dumps.pool("raiders")])


def test_materialisation_can_be_switched_off(tmp_path: Path) -> None:
    result = planned(tmp_path, [2001], global_pool="", banpools=[dumps.pool("raiders")])

    assert result.subscriptions == []


# -- duplicates --------------------------------------------------------------


def test_a_duplicate_pool_keeps_the_earlier_and_says_so(tmp_path: Path) -> None:
    """Every old `add_*` refused the second write, so the row a moderator saw succeed is
    the earlier one."""
    result = planned(
        tmp_path,
        [2001],
        banpools=[
            dumps.pool("global", "first", days=0),
            dumps.pool("global", "second", days=5),
        ],
    )

    assert [pool.description for pool in result.pools] == ["first"]
    assert plan.Anomaly.DUPLICATE_POOL in result.anomalies_by_kind()


def test_a_duplicate_listing_keeps_the_earlier(tmp_path: Path) -> None:
    result = planned(
        tmp_path,
        [2001],
        banpools=[dumps.pool("global")],
        bans=[
            dumps.ban(1001, "global", reason="first", days=0),
            dumps.ban(1001, "global", reason="second", days=5),
        ],
    )

    assert [listing.reason for listing in result.listings] == ["first"]
    assert plan.Anomaly.DUPLICATE_LISTING in result.anomalies_by_kind()


def test_a_subscription_held_at_two_levels_keeps_ban(tmp_path: Path) -> None:
    """Against "first write wins", because what the old bot *did* is what is being
    preserved, and its live check banned regardless of the level."""
    result = planned(
        tmp_path,
        [2001],
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        subscriptions=[
            dumps.subscription(2001, "raiders", "warn", days=0),
            dumps.subscription(2001, "raiders", "ban", days=5),
        ],
    )
    raiders = result.pool_ids_by_name()["raiders"]

    levels = {
        subscription.level
        for subscription in result.subscriptions
        if subscription.pool_id == raiders
    }

    assert levels == {SubscriptionLevel.BAN}
    assert plan.Anomaly.SUBSCRIPTION_LEVEL_CONFLICT in result.anomalies_by_kind()


def test_duplicate_exceptions_and_channels_collapse(tmp_path: Path) -> None:
    result = planned(
        tmp_path,
        [2001],
        banpools=[dumps.pool("global")],
        exceptions=[dumps.exception(2001, 1002), dumps.exception(2001, 1002, days=1)],
        notifications=[
            dumps.notification(2001, 3001),
            dumps.notification(2001, 3002, days=1),
        ],
    )

    assert len(result.exceptions) == 1
    assert [channel.channel_id for channel in result.notification_channels] == [3001]
    assert plan.Anomaly.DUPLICATE_EXCEPTION in result.anomalies_by_kind()
    assert plan.Anomaly.DUPLICATE_NOTIFICATION_CHANNEL in result.anomalies_by_kind()


# -- orphans and departures --------------------------------------------------


def test_a_listing_in_a_deleted_pool_is_dropped_and_reported(tmp_path: Path) -> None:
    """The old `delete_pool` deleted one document and cascaded nothing, so every pool
    ever deleted left its bans behind."""
    result = planned(
        tmp_path,
        [2001],
        banpools=[dumps.pool("global")],
        bans=[dumps.ban(1001, "gone-in-2021")],
    )

    assert result.listings == []
    assert result.anomalies_by_kind()[plan.Anomaly.ORPHAN_LISTING] == [
        "user 1001 in pool 'gone-in-2021'"
    ]


def test_a_subscription_to_a_deleted_pool_is_dropped_and_reported(
    tmp_path: Path,
) -> None:
    result = planned(
        tmp_path,
        [2001],
        banpools=[dumps.pool("global")],
        subscriptions=[dumps.subscription(2001, "gone-in-2021")],
    )

    assert plan.Anomaly.ORPHAN_SUBSCRIPTION in result.anomalies_by_kind()


def test_rows_for_departed_guilds_are_dropped(tmp_path: Path) -> None:
    """Importing them would give the sweep a guild to fail against every hour forever."""
    result = planned(
        tmp_path,
        [2001],
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        subscriptions=[dumps.subscription(9999, "raiders")],
        exceptions=[dumps.exception(9999, 1002)],
        notifications=[dumps.notification(9999, 3001)],
    )

    assert result.exceptions == []
    assert result.notification_channels == []
    assert [guild.guild_id for guild in result.guilds] == [2001]
    assert len(result.anomalies_by_kind()[plan.Anomaly.DEPARTED_GUILD]) == 3


def test_the_guilds_table_is_the_snapshot_exactly(import_plan: plan.ImportPlan) -> None:
    assert [guild.guild_id for guild in import_plan.guilds] == [2001, 2002, 2003]


def test_guilds_are_dated_from_the_snapshot(
    import_plan: plan.ImportPlan, snapshot: guilds.Snapshot
) -> None:
    """The only thing actually known: Discord's listing carries no join date, and Mongo
    never recorded one."""
    assert all(guild.joined_at == snapshot.fetched_at for guild in import_plan.guilds)


# -- reproducibility ---------------------------------------------------------


def test_the_same_inputs_give_the_same_plan(
    source: records.Source, snapshot: guilds.Snapshot
) -> None:
    """The property the whole dump-based approach exists for: a rehearsal is evidence
    about the real run only if the real run does the same thing."""
    first = plan.build(source, snapshot)
    second = plan.build(source, snapshot)

    assert first == second
