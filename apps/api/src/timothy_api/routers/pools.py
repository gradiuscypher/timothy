"""Pools: the named lists guilds subscribe to.

Owned by whoever holds a pool manager role in the management guild (ADR 0012), which is
not the same people as that guild's administrators. Readable by anyone in a guild Timothy
is in, which is the rule ADR 0001 expects to relax first.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from timothy_api import audit, jobs
from timothy_api.deps import Requires, SessionDep
from timothy_api.lookups import conflict, find_pool, get_pool
from timothy_api.policy import Operation
from timothy_api.schemas import PoolCreate, PoolRead, PoolUpdate
from timothy_core.actors import Actor
from timothy_core.db.models import Pool

router = APIRouter(prefix="/pools", tags=["pools"])

Manager = Annotated[Actor, Depends(Requires(Operation.MANAGE_POOLS))]
Reader = Annotated[Actor, Depends(Requires(Operation.READ_POOLS))]

Revert = Annotated[
    bool,
    Query(
        description=(
            "Lift the bans this removal leaves unjustified. Off by default: reverting is "
            "only ever safe for bans Timothy has a recorded outcome for (ADR 0005)."
        )
    ),
]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_pool(body: PoolCreate, actor: Manager, session: SessionDep) -> PoolRead:
    """Create a pool."""
    if await find_pool(session, body.name) is not None:
        raise conflict(f"pool already exists: {body.name}")

    pool = Pool(name=body.name, description=body.description, created_by=actor)
    session.add(pool)
    await session.flush()

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.POOL_CREATE,
        target=audit.pool_target(pool.name),
        detail={"pool_id": pool.id},
    )
    await session.commit()
    return PoolRead.of(pool)


@router.get("")
async def list_pools(_actor: Reader, session: SessionDep) -> list[PoolRead]:
    """Every pool, by name."""
    pools = await session.scalars(select(Pool).order_by(Pool.name))
    return [PoolRead.of(pool) for pool in pools]


@router.get("/{name}")
async def read_pool(name: str, _actor: Reader, session: SessionDep) -> PoolRead:
    """One pool."""
    return PoolRead.of(await get_pool(session, name))


@router.patch("/{name}")
async def update_pool(
    name: str, body: PoolUpdate, actor: Manager, session: SessionDep
) -> PoolRead:
    """Rename a pool, or change its description.

    A rename rewrites nothing else: listings and subscriptions reference the surrogate
    key, which is why the key is there.
    """
    pool = await get_pool(session, name)
    changed: dict[str, object] = {}

    if body.name is not None and body.name != pool.name:
        if await find_pool(session, body.name) is not None:
            raise conflict(f"pool already exists: {body.name}")
        changed["name"] = {"from": pool.name, "to": body.name}
        pool.name = body.name

    if body.description != pool.description:
        changed["description"] = {"from": pool.description, "to": body.description}
        pool.description = body.description

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.POOL_UPDATE,
        target=audit.pool_target(name),
        detail={"pool_id": pool.id, "changed": changed},
    )
    await session.commit()
    return PoolRead.of(pool)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pool(
    name: str,
    actor: Manager,
    session: SessionDep,
    *,
    revert: Revert = False,
) -> None:
    """Delete a pool, its listings and every subscription to it.

    The enforcement outcomes it caused survive, holding no foreign key to it — which is
    what leaves `revert` able to find the bans afterwards.
    """
    pool = await get_pool(session, name)
    pool_id = pool.id

    if revert:
        jobs.enqueue(session, jobs.JobKind.REVERT_POOL, pool_id=pool_id)

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.POOL_DELETE,
        target=audit.pool_target(name),
        detail={"pool_id": pool_id, "revert": revert},
    )
    await session.delete(pool)
    await session.commit()
