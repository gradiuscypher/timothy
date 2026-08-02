"""Listings: the record that a user belongs on a pool.

A listing is an assertion, not an action — creating one bans nobody by itself. What it
does is enqueue the enforcement that ADR 0004 makes immediate, which phase 3's workers
carry out against the guilds subscribing at that moment.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import select

from timothy_api import audit, jobs
from timothy_api.deps import Requires, SessionDep
from timothy_api.lookups import conflict, find_listing, get_pool, not_found
from timothy_api.policy import Operation
from timothy_api.schemas import ListingCreate, ListingRead, Snowflake
from timothy_core.actors import Actor
from timothy_core.db.models import Listing, Pool

router = APIRouter(tags=["listings"])

Manager = Annotated[Actor, Depends(Requires(Operation.MANAGE_LISTINGS))]
Reader = Annotated[Actor, Depends(Requires(Operation.READ_POOLS))]

UserId = Annotated[Snowflake, Path(description="A Discord user ID.")]
Revert = Annotated[
    bool,
    Query(
        description=(
            "Lift the bans this removal leaves unjustified. Off by default: reverting is "
            "only ever safe for bans Timothy has a recorded outcome for (ADR 0005)."
        )
    ),
]


@router.post("/pools/{name}/listings", status_code=status.HTTP_201_CREATED)
async def create_listing(
    name: str, body: ListingCreate, actor: Manager, session: SessionDep
) -> ListingRead:
    """List a user on a pool, and enqueue the enforcement that follows."""
    pool = await get_pool(session, name)
    if await find_listing(session, pool_id=pool.id, user_id=body.user_id) is not None:
        raise conflict(f"already listed in {pool.name}: {body.user_id}")

    listing = Listing(
        user_id=body.user_id,
        pool_id=pool.id,
        reason=body.reason,
        created_by=actor,
    )
    session.add(listing)
    await session.flush()

    jobs.enqueue(session, jobs.JobKind.ENFORCE_LISTING, listing_id=listing.id)
    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.LISTING_CREATE,
        target=audit.listing_target(pool_name=pool.name, user_id=listing.user_id),
        detail={"listing_id": listing.id, "pool_id": pool.id, "reason": listing.reason},
    )
    await session.commit()
    return ListingRead.of(listing, pool)


@router.get("/pools/{name}/listings")
async def list_pool_listings(
    name: str, _actor: Reader, session: SessionDep
) -> list[ListingRead]:
    """Everyone listed on one pool."""
    pool = await get_pool(session, name)
    listings = await session.scalars(
        select(Listing).where(Listing.pool_id == pool.id).order_by(Listing.created_at)
    )
    return [ListingRead.of(listing, pool) for listing in listings]


@router.get("/users/{user_id}/listings")
async def list_user_listings(
    user_id: UserId, _actor: Reader, session: SessionDep
) -> list[ListingRead]:
    """Why this user is listed, across every pool.

    This is the lookup ADR 0001 names as the first rule it expects to relax — today it
    needs membership of some guild Timothy is in, and the intent is a subscribing guild's
    own moderators. Changing that is a line in :mod:`timothy_api.policy`.
    """
    rows = await session.execute(
        select(Listing, Pool)
        .join(Pool, Pool.id == Listing.pool_id)
        .where(Listing.user_id == user_id)
        .order_by(Pool.name)
    )
    return [ListingRead.of(listing, pool) for listing, pool in rows]


@router.delete("/pools/{name}/listings/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_listing(
    name: str,
    user_id: UserId,
    actor: Manager,
    session: SessionDep,
    *,
    revert: Revert = False,
) -> None:
    """Remove a listing, optionally lifting the bans it was holding up."""
    pool = await get_pool(session, name)
    listing = await find_listing(session, pool_id=pool.id, user_id=user_id)
    if listing is None:
        raise not_found(f"listing: {user_id} in {pool.name}")

    if revert:
        jobs.enqueue(session, jobs.JobKind.REVERT_LISTING, pool_id=pool.id, user_id=user_id)

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.LISTING_DELETE,
        target=audit.listing_target(pool_name=pool.name, user_id=user_id),
        detail={"listing_id": listing.id, "pool_id": pool.id, "revert": revert},
    )
    await session.delete(listing)
    await session.commit()
