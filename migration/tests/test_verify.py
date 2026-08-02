"""The static check: every decision, both systems, on the imported database.

This is the test suite for the thing that decides whether the cutover happens, so what it
mostly asserts is that the check can *fail* — a comparison that always says "ready" would
pass a review and prove nothing.
"""

from collections.abc import Callable
from pathlib import Path

import dumps
import pytest

from timothy_core.enums import SubscriptionLevel
from timothy_migration import check, guilds, load, plan, records
from timothy_migration.dump import Dump
from timothy_migration.oldbot import OldBot


async def compare(
    tmp_path: Path,
    database: Path,
    guild_ids: list[int],
    *,
    mangle: Callable[[plan.ImportPlan], None] | None = None,
    **collections: list[dict[str, object]],
) -> check.Comparison:
    """Import a dump, then check the result against it.

    `mangle` is a callable given the plan before it is written, so a test can break the
    import on purpose and watch the check notice.
    """
    source = records.read(Dump(dumps.build(tmp_path / "dump", **collections)))  # type: ignore[arg-type]
    snapshot = guilds.Snapshot.read(dumps.snapshot(tmp_path / "guilds.json", guild_ids))
    import_plan = plan.build(source, snapshot)
    if mangle is not None:
        mangle(import_plan)

    await load.load(import_plan, database)
    imported = await check.read_imported(database)
    return check.verify(imported, OldBot.from_source(source))


@pytest.mark.anyio
async def test_a_faithful_import_agrees_everywhere(
    import_plan: plan.ImportPlan, source: records.Source, database: Path
) -> None:
    await load.load(import_plan, database)
    imported = await check.read_imported(database)

    comparison = check.verify(imported, OldBot.from_source(source))

    assert comparison.unexplained == []
    assert comparison.pairs_compared == 6  # three guilds, two listed users


@pytest.mark.anyio
async def test_the_guild_that_configured_nothing_still_enforces_global(
    import_plan: plan.ImportPlan, source: records.Source, database: Path
) -> None:
    """The whole reason `global` is materialised. Guild 2003 has no rows in the dump at
    all, and rode the old hardcoded short-circuit."""
    await load.load(import_plan, database)
    imported = await check.read_imported(database)

    comparison = check.verify(imported, OldBot.from_source(source))
    for_2003 = [finding for finding in comparison.findings if finding.guild_id == 2003]

    assert for_2003 == []


@pytest.mark.anyio
async def test_a_warn_subscription_is_reported_as_an_intended_change(
    tmp_path: Path, database: Path
) -> None:
    """The old bot banned this user on join; Timothy warns. Intended, and somebody has to
    agree to it per guild rather than find out afterwards."""
    comparison = await compare(
        tmp_path,
        database,
        [2001],
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        bans=[dumps.ban(1001, "raiders")],
        subscriptions=[dumps.subscription(2001, "raiders", "warn")],
    )

    assert [finding.verdict for finding in comparison.findings] == [check.Verdict.NOW_WARNS]
    assert comparison.unexplained == []


@pytest.mark.anyio
async def test_a_dead_pool_is_reported_as_no_longer_enforced(
    tmp_path: Path, database: Path
) -> None:
    """The old `delete_pool` left the bans behind and `is_guild_subscribed` matched the
    dead name, so this guild really was enforcing a pool that had not existed for years."""
    comparison = await compare(
        tmp_path,
        database,
        [2001],
        banpools=[dumps.pool("global")],
        bans=[dumps.ban(1001, "gone-in-2021")],
        subscriptions=[dumps.subscription(2001, "gone-in-2021")],
    )

    assert [finding.verdict for finding in comparison.findings] == [
        check.Verdict.NO_LONGER_ENFORCED
    ]
    assert "'gone-in-2021' no longer exists as a pool" in comparison.findings[0].detail
    assert comparison.unexplained == []


@pytest.mark.anyio
async def test_an_invented_subscription_is_caught(tmp_path: Path, database: Path) -> None:
    """The bucket that has to be empty. Nothing in the migration is supposed to reach
    it, which is exactly why it is worth being able to reach on purpose."""

    def add_a_subscription(import_plan: plan.ImportPlan) -> None:
        import_plan.subscriptions.append(
            plan.PlannedSubscription(
                guild_id=2002,
                pool_id=import_plan.pool_ids_by_name()["raiders"],
                level=SubscriptionLevel.BAN,
                created_by=import_plan.pools[0].created_by,
                created_at=import_plan.pools[0].created_at,
            )
        )

    comparison = await compare(
        tmp_path,
        database,
        [2001, 2002],
        mangle=add_a_subscription,
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        bans=[dumps.ban(1001, "raiders")],
        subscriptions=[dumps.subscription(2001, "raiders")],
    )

    assert [finding.verdict for finding in comparison.unexplained] == [
        check.Verdict.NEWLY_ENFORCED
    ]
    assert comparison.unexplained[0].guild_id == 2002


@pytest.mark.anyio
async def test_a_lost_exception_is_caught(tmp_path: Path, database: Path) -> None:
    """An exception that did not survive the import turns a user the guild vouched for
    into a user Timothy bans."""

    def drop_the_exceptions(import_plan: plan.ImportPlan) -> None:
        import_plan.exceptions.clear()

    comparison = await compare(
        tmp_path,
        database,
        [2001],
        mangle=drop_the_exceptions,
        banpools=[dumps.pool("global")],
        bans=[dumps.ban(1001, "global")],
        exceptions=[dumps.exception(2001, 1001)],
    )

    assert [finding.verdict for finding in comparison.unexplained] == [
        check.Verdict.NEWLY_ENFORCED
    ]


@pytest.mark.anyio
async def test_a_lost_listing_is_caught(tmp_path: Path, database: Path) -> None:
    """The under-enforcement direction, which the dry run diff cannot see at all."""

    def drop_the_listings(import_plan: plan.ImportPlan) -> None:
        import_plan.listings.clear()

    comparison = await compare(
        tmp_path,
        database,
        [2001],
        mangle=drop_the_listings,
        banpools=[dumps.pool("global")],
        bans=[dumps.ban(1001, "global")],
    )

    assert [finding.verdict for finding in comparison.findings] == [
        check.Verdict.NO_LONGER_ENFORCED
    ]
    assert "did not survive the import" in comparison.findings[0].detail


@pytest.mark.anyio
async def test_the_tally_accounts_for_every_pair(tmp_path: Path, database: Path) -> None:
    comparison = await compare(
        tmp_path,
        database,
        [2001, 2002],
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        bans=[dumps.ban(1001, "raiders"), dumps.ban(1002, "global")],
        subscriptions=[dumps.subscription(2001, "raiders", "warn")],
    )

    assert sum(comparison.tally().values()) == comparison.pairs_compared


@pytest.mark.anyio
async def test_a_paused_guild_is_not_read_as_a_difference(
    import_plan: plan.ImportPlan, source: records.Source, database: Path
) -> None:
    """`enforcement_paused` is a rail, not a policy. The import never sets it, and the
    check reads it so that a database paused by hand between import and verify reports
    honestly rather than looking like a lost subscription."""
    await load.load(import_plan, database)
    imported = await check.read_imported(database)

    assert all(not state.enforcement_paused for state in imported.subscriptions.values())


@pytest.mark.anyio
async def test_an_invented_warn_subscription_is_caught(tmp_path: Path, database: Path) -> None:
    """The other half of `newly enforced`: warning about a user nothing would have
    touched is still Timothy acting where the old bot did not."""

    def add_a_warn_subscription(import_plan: plan.ImportPlan) -> None:
        import_plan.subscriptions.append(
            plan.PlannedSubscription(
                guild_id=2002,
                pool_id=import_plan.pool_ids_by_name()["raiders"],
                level=SubscriptionLevel.WARN,
                created_by=import_plan.pools[0].created_by,
                created_at=import_plan.pools[0].created_at,
            )
        )

    comparison = await compare(
        tmp_path,
        database,
        [2001, 2002],
        mangle=add_a_warn_subscription,
        banpools=[dumps.pool("global"), dumps.pool("raiders")],
        bans=[dumps.ban(1001, "raiders")],
        subscriptions=[dumps.subscription(2001, "raiders")],
    )

    assert [finding.verdict for finding in comparison.unexplained] == [
        check.Verdict.NEWLY_ENFORCED
    ]
    assert "would warn" in comparison.unexplained[0].detail
