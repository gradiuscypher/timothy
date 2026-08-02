"""Listings: the record that a user belongs on a pool.

A listing is an assertion, not an action — creating one bans nobody by itself. What it
does is enqueue the enforcement that ADR 0004 makes immediate, which phase 3's workers
carry out against the guilds subscribing at that moment.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import ColumnElement, cast, func, or_, select
from sqlalchemy.types import String

from timothy_api import audit, jobs
from timothy_api.deps import Requires, SessionDep
from timothy_api.lookups import conflict, find_listing, get_pool, not_found
from timothy_api.policy import Operation
from timothy_api.schemas import (
    BulkListingCreate,
    BulkListingDelete,
    BulkResult,
    ListingCreate,
    ListingPage,
    ListingRead,
    Snowflake,
)
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

Limit = Annotated[int, Query(ge=1, le=200, description="How many listings to return.")]
After = Annotated[
    int | None,
    Query(gt=0, description="Return listings after this id. Omit for the first page."),
]
Search = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=128,
        description="Match against the reason, or against the user ID as text.",
    ),
]

LIKE_ESCAPE = "\\"


def _matches(query: str) -> ColumnElement[bool]:
    """A listing whose reason or user ID contains this text.

    The user ID is compared as *text* so that a partial snowflake works: somebody
    reading a screenshot types the six digits they can make out, not all eighteen.
    `%` and `_` in the query are escaped, because a moderator searching for `100%` is
    searching for a string and not writing a pattern.
    """
    pattern = query
    for character in (LIKE_ESCAPE, "%", "_"):
        pattern = pattern.replace(character, LIKE_ESCAPE + character)
    pattern = f"%{pattern}%"
    return or_(
        Listing.reason.ilike(pattern, escape=LIKE_ESCAPE),
        cast(Listing.user_id, String).like(pattern, escape=LIKE_ESCAPE),
    )


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
    name: str,
    _actor: Reader,
    session: SessionDep,
    limit: Limit = 50,
    after_id: After = None,
    q: Search = None,
) -> ListingPage:
    """Everyone listed on one pool, a page at a time.

    Paginated because `global` is not a list anybody scrolls: the migrated data has
    thousands of listings on it, and phase 4's `/list_pools` timeout is what happens when
    a route that was fine at ten rows meets production.

    Ordered by id, which is also the page cursor. `created_at` is what a human reads but
    it is not unique, and a cursor that can repeat a value can skip a row.
    """
    pool = await get_pool(session, name)
    matching = [Listing.pool_id == pool.id]
    if q is not None:
        matching.append(_matches(q))

    total = await session.scalar(select(func.count()).select_from(Listing).where(*matching))
    page = select(Listing).where(*matching).order_by(Listing.id).limit(limit)
    if after_id is not None:
        page = page.where(Listing.id > after_id)

    listings = list(await session.scalars(page))
    return ListingPage(
        listings=[ListingRead.of(listing, pool) for listing in listings],
        next_after_id=listings[-1].id if len(listings) == limit else None,
        total=total or 0,
    )


@router.post("/pools/{name}/listings/bulk")
async def create_listings(
    name: str, body: BulkListingCreate, actor: Manager, session: SessionDep
) -> BulkResult:
    """List many users on a pool at once, and enqueue enforcement for each.

    Web-only: no slash command sends this, because a text box in Discord is not where
    anybody should be pasting three hundred user IDs.

    One job per new listing rather than one job for the batch. The worker's unit of work
    is a listing fanned out across subscribing guilds (phase 3), and a batch-shaped job
    would need its own retry and its own accounting against ADR 0007's breaker for no
    benefit — the queue is a table, and rows are cheap.

    Users already listed are skipped and reported, not treated as failures. Duplicates
    within one request collapse the same way, so pasting a list with repeats in it does
    what the person meant.

    One audit row per listing, exactly as if they had been added one at a time, because
    "why is this user listed and who did it" is the question the log exists to answer and
    a single summary row cannot. The rows carry `bulk: true` so a batch is still
    recognisable as one.
    """
    pool = await get_pool(session, name)
    applied: list[int] = []
    skipped: list[int] = []

    for user_id in dict.fromkeys(body.user_ids):
        if await find_listing(session, pool_id=pool.id, user_id=user_id) is not None:
            skipped.append(user_id)
            continue

        listing = Listing(
            user_id=user_id, pool_id=pool.id, reason=body.reason, created_by=actor
        )
        session.add(listing)
        await session.flush()
        jobs.enqueue(session, jobs.JobKind.ENFORCE_LISTING, listing_id=listing.id)
        audit.record(
            session,
            actor=actor,
            action=audit.AuditAction.LISTING_CREATE,
            target=audit.listing_target(pool_name=pool.name, user_id=user_id),
            detail={
                "listing_id": listing.id,
                "pool_id": pool.id,
                "reason": body.reason,
                "bulk": True,
            },
        )
        applied.append(user_id)

    await session.commit()
    return BulkResult(applied=applied, skipped=skipped)


@router.post("/pools/{name}/listings/bulk-delete")
async def delete_listings(
    name: str,
    body: BulkListingDelete,
    actor: Manager,
    session: SessionDep,
    *,
    revert: Revert = False,
) -> BulkResult:
    """Remove many listings from a pool at once.

    A POST rather than a DELETE with a body: a request body on DELETE is permitted but
    not reliably carried, and the one operation where a proxy silently dropping the body
    would mean "delete nothing" is not the place to find out which proxies those are.

    `revert` lifts the bans each removal leaves unjustified, and is off by default for
    the same reason it is everywhere else (ADR 0005).
    """
    pool = await get_pool(session, name)
    applied: list[int] = []
    skipped: list[int] = []

    for user_id in dict.fromkeys(body.user_ids):
        listing = await find_listing(session, pool_id=pool.id, user_id=user_id)
        if listing is None:
            skipped.append(user_id)
            continue
        if revert:
            jobs.enqueue(session, jobs.JobKind.REVERT_LISTING, pool_id=pool.id, user_id=user_id)
        audit.record(
            session,
            actor=actor,
            action=audit.AuditAction.LISTING_DELETE,
            target=audit.listing_target(pool_name=pool.name, user_id=user_id),
            detail={
                "listing_id": listing.id,
                "pool_id": pool.id,
                "revert": revert,
                "bulk": True,
            },
        )
        await session.delete(listing)
        applied.append(user_id)

    await session.commit()
    return BulkResult(applied=applied, skipped=skipped)


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
