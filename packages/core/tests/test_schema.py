"""What the database itself refuses to let happen."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from timothy_core.actors import Actor
from timothy_core.db.models import (
    EnforcementOutcome,
    Guild,
    GuildException,
    Listing,
    Pool,
    Subscription,
)
from timothy_core.enums import OutcomeStatus, SubscriptionLevel

pytestmark = pytest.mark.anyio

MODERATOR = Actor.user(1)
GUILD_ID = 111
USER_ID = 222


async def _seed(session: AsyncSession) -> Pool:
    session.add(Guild(guild_id=GUILD_ID))
    pool = Pool(name="global", description="the shared banlist", created_by=MODERATOR)
    session.add(pool)
    await session.flush()
    return pool


async def test_a_user_is_listed_at_most_once_per_pool(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        pool = await _seed(session)
        session.add(
            Listing(user_id=USER_ID, pool_id=pool.id, reason="spam", created_by=MODERATOR)
        )
        await session.flush()

        session.add(
            Listing(user_id=USER_ID, pool_id=pool.id, reason="again", created_by=MODERATOR)
        )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_foreign_keys_are_enforced(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """SQLite ships with foreign keys off; every cascade below is inert without the pragma."""
    async with sessions() as session:
        pool = await _seed(session)
        session.add(
            Subscription(
                guild_id=999,
                pool_id=pool.id,
                level=SubscriptionLevel.BAN,
                created_by=MODERATOR,
            ),
        )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_deleting_a_pool_takes_its_listings_and_subscriptions(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        pool = await _seed(session)
        session.add(
            Listing(user_id=USER_ID, pool_id=pool.id, reason="spam", created_by=MODERATOR)
        )
        session.add(
            Subscription(
                guild_id=GUILD_ID,
                pool_id=pool.id,
                level=SubscriptionLevel.BAN,
                created_by=MODERATOR,
            ),
        )
        await session.commit()

        await session.execute(delete(Pool).where(Pool.id == pool.id))
        await session.commit()

        assert (await session.execute(select(Listing))).scalars().all() == []
        assert (await session.execute(select(Subscription))).scalars().all() == []


async def test_enforcement_outcomes_outlive_the_pool_they_name(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """ADR 0005: outcomes are what make a ban revertable, so nothing cascades them away."""
    async with sessions() as session:
        pool = await _seed(session)
        session.add(
            EnforcementOutcome(
                guild_id=GUILD_ID,
                user_id=USER_ID,
                pool_id=pool.id,
                status=OutcomeStatus.BANNED,
                reason="spam",
            ),
        )
        await session.commit()

        await session.execute(delete(Pool).where(Pool.id == pool.id))
        await session.commit()

        outcomes = (await session.execute(select(EnforcementOutcome))).scalars().all()
        assert [outcome.status for outcome in outcomes] == [OutcomeStatus.BANNED]


async def test_one_outcome_per_guild_user_and_pool(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The composite key is the warn-dedupe key, so a second row must be impossible."""
    async with sessions() as session:
        pool = await _seed(session)
        for _ in range(2):
            session.add(
                EnforcementOutcome(
                    guild_id=GUILD_ID,
                    user_id=USER_ID,
                    pool_id=pool.id,
                    status=OutcomeStatus.WARNED,
                    reason="spam",
                ),
            )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_leaving_a_guild_takes_its_configuration(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        pool = await _seed(session)
        session.add(
            Subscription(
                guild_id=GUILD_ID,
                pool_id=pool.id,
                level=SubscriptionLevel.WARN,
                created_by=MODERATOR,
            ),
        )
        session.add(
            GuildException(
                guild_id=GUILD_ID, user_id=USER_ID, reason=None, created_by=MODERATOR
            ),
        )
        await session.commit()

        await session.execute(delete(Guild).where(Guild.guild_id == GUILD_ID))
        await session.commit()

        assert (await session.execute(select(Subscription))).scalars().all() == []
        assert (await session.execute(select(GuildException))).scalars().all() == []


async def test_actors_round_trip_through_the_database(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        session.add(Pool(name="by-a-person", created_by=Actor.user(7)))
        session.add(Pool(name="by-timothy", created_by=Actor.system()))
        await session.commit()
        session.expire_all()

        by_name = {
            pool.name: pool.created_by
            for pool in (await session.execute(select(Pool))).scalars()
        }

    assert by_name == {"by-a-person": Actor.user(7), "by-timothy": Actor.system()}


async def test_enums_are_stored_as_their_values(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """PLAN.md's schema says `level ('ban'|'warn')`, and the database should say so too."""
    async with sessions() as session:
        pool = await _seed(session)
        session.add(
            Subscription(
                guild_id=GUILD_ID,
                pool_id=pool.id,
                level=SubscriptionLevel.BAN,
                created_by=MODERATOR,
            ),
        )
        await session.commit()

        stored = await session.execute(text("SELECT level FROM subscriptions"))
        assert stored.scalar_one() == "ban"


async def test_an_out_of_range_level_is_refused(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        pool = await _seed(session)
        await session.commit()

        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO subscriptions"
                    " (guild_id, pool_id, level, created_by, created_at)"
                    " VALUES (:guild, :pool, 'kick', 'system', '2026-01-01 00:00:00')",
                ),
                {"guild": GUILD_ID, "pool": pool.id},
            )


async def test_timestamps_come_back_aware_and_in_utc(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    written = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=5)))

    async with sessions() as session:
        session.add(Pool(name="tz", created_by=MODERATOR, created_at=written))
        await session.commit()
        session.expire_all()

        pool = (await session.execute(select(Pool))).scalar_one()

    assert pool.created_at.tzinfo is UTC
    assert pool.created_at == written
    assert pool.created_at.hour == 7


async def test_a_naive_timestamp_is_refused(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Guessing at the offset is how a sweep window ends up five hours wide."""
    async with sessions() as session:
        session.add(Pool(name="naive", created_by=MODERATOR, created_at=datetime(2026, 1, 1)))  # noqa: DTZ001
        with pytest.raises(StatementError, match="naive datetime"):
            await session.flush()


async def test_defaults_fill_themselves_in(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        session.add(Guild(guild_id=GUILD_ID))
        await session.commit()
        session.expire_all()

        guild = (await session.execute(select(Guild))).scalar_one()

    assert guild.enforcement_paused is False
    assert guild.joined_at.tzinfo is UTC
