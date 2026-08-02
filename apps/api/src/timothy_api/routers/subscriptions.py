"""Subscriptions: a guild's decision to enforce a pool, at ban or warn level.

Every subscription is a real row, including the one to the shared pool — ADR 0002 dropped
the reserved name that made it un-leavable, so what is listed here is what is actually
stored.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import select

from timothy_api import audit, jobs
from timothy_api.deps import Requires, SessionDep
from timothy_api.lookups import find_subscription, get_guild, get_pool, not_found
from timothy_api.policy import Operation
from timothy_api.schemas import Snowflake, SubscriptionRead, SubscriptionSet
from timothy_core.actors import Actor
from timothy_core.db.models import Pool, Subscription
from timothy_core.enums import SubscriptionLevel

router = APIRouter(prefix="/guilds/{guild_id}/subscriptions", tags=["subscriptions"])

Manager = Annotated[Actor, Depends(Requires(Operation.MANAGE_SUBSCRIPTIONS))]

GuildId = Annotated[Snowflake, Path(description="A Discord guild ID.")]
Revert = Annotated[
    bool,
    Query(
        description=(
            "Lift the bans this unsubscribe leaves unjustified. Off by default: only bans "
            "with a recorded enforcement outcome are ever touched (ADR 0005)."
        )
    ),
]


@router.get("")
async def list_subscriptions(
    guild_id: GuildId, _actor: Manager, session: SessionDep
) -> list[SubscriptionRead]:
    """What this guild has subscribed to."""
    await get_guild(session, guild_id)
    rows = await session.execute(
        select(Subscription, Pool)
        .join(Pool, Pool.id == Subscription.pool_id)
        .where(Subscription.guild_id == guild_id)
        .order_by(Pool.name)
    )
    return [SubscriptionRead.of(subscription, pool) for subscription, pool in rows]


@router.put("/{pool_name}")
async def set_subscription(
    guild_id: GuildId,
    pool_name: str,
    body: SubscriptionSet,
    actor: Manager,
    session: SessionDep,
) -> SubscriptionRead:
    """Subscribe to a pool, or change the level of an existing subscription.

    Enqueues enforcement when the level is new or has been raised. Lowering ban to warn
    enqueues nothing and lifts nothing — the guild asked to stop banning from now on, not
    to undo what it already did, and undoing it is what `revert` on the delete is for.
    """
    await get_guild(session, guild_id)
    pool = await get_pool(session, pool_name)

    subscription = await find_subscription(session, guild_id=guild_id, pool_id=pool.id)
    previous = subscription.level if subscription is not None else None

    if subscription is None:
        subscription = Subscription(
            guild_id=guild_id,
            pool_id=pool.id,
            level=body.level,
            created_by=actor,
        )
        session.add(subscription)
    else:
        subscription.level = body.level

    raised = previous is None or (
        previous is not body.level and body.level is SubscriptionLevel.BAN
    )
    if raised:
        jobs.enqueue(
            session,
            jobs.JobKind.ENFORCE_SUBSCRIPTION,
            guild_id=guild_id,
            pool_id=pool.id,
        )

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.SUBSCRIPTION_SET,
        target=audit.guild_pool_target(guild_id=guild_id, pool_name=pool.name),
        detail={
            "pool_id": pool.id,
            "level": body.level.value,
            "previous_level": previous.value if previous is not None else None,
        },
    )
    await session.commit()
    return SubscriptionRead.of(subscription, pool)


@router.delete("/{pool_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    guild_id: GuildId,
    pool_name: str,
    actor: Manager,
    session: SessionDep,
    *,
    revert: Revert = False,
) -> None:
    """Unsubscribe, optionally lifting the bans this pool was holding up here."""
    await get_guild(session, guild_id)
    pool = await get_pool(session, pool_name)

    subscription = await find_subscription(session, guild_id=guild_id, pool_id=pool.id)
    if subscription is None:
        raise not_found(f"subscription: {pool.name} in guild {guild_id}")

    if revert:
        jobs.enqueue(
            session,
            jobs.JobKind.REVERT_SUBSCRIPTION,
            guild_id=guild_id,
            pool_id=pool.id,
        )

    audit.record(
        session,
        actor=actor,
        action=audit.AuditAction.SUBSCRIPTION_DELETE,
        target=audit.guild_pool_target(guild_id=guild_id, pool_name=pool.name),
        detail={"pool_id": pool.id, "revert": revert},
    )
    await session.delete(subscription)
    await session.commit()
