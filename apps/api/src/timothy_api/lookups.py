"""Fetching the row a request names, or saying plainly that it is not there.

Pools resolve by name because the name is what humans type — slash commands and API
paths both — while the surrogate key is what everything else references, so a pool can be
renamed without rewriting a single listing or subscription.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timothy_core.db.models import Guild, Listing, Pool, Subscription


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such {what}")


async def find_pool(session: AsyncSession, name: str) -> Pool | None:
    """The pool with this name, if there is one."""
    return await session.scalar(select(Pool).where(Pool.name == name))


async def get_pool(session: AsyncSession, name: str) -> Pool:
    """The pool with this name.

    Raises:
        HTTPException: 404 if no pool has that name.
    """
    pool = await find_pool(session, name)
    if pool is None:
        raise _not_found(f"pool: {name}")
    return pool


async def find_listing(session: AsyncSession, *, pool_id: int, user_id: int) -> Listing | None:
    """This user's listing on this pool, if there is one."""
    return await session.scalar(
        select(Listing).where(Listing.pool_id == pool_id, Listing.user_id == user_id)
    )


async def get_guild(session: AsyncSession, guild_id: int) -> Guild:
    """A guild Timothy is in.

    Raises:
        HTTPException: 404 if Timothy has no record of being in it.
    """
    guild = await session.get(Guild, guild_id)
    if guild is None:
        raise _not_found(f"guild: {guild_id}")
    return guild


async def find_subscription(
    session: AsyncSession, *, guild_id: int, pool_id: int
) -> Subscription | None:
    """This guild's subscription to this pool, if it has one."""
    return await session.get(Subscription, (guild_id, pool_id))


def conflict(detail: str) -> HTTPException:
    """The row is already there, and creating it again would mean something different."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def not_found(what: str) -> HTTPException:
    """Nothing here by that name."""
    return _not_found(what)
