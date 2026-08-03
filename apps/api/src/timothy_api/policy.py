"""Who may do what. The only place that answers it (ADR 0001).

Authority is *derived* from Discord rather than stored: holding a configured role in the
management guild owns pools and listings, holding `ADMINISTRATOR` in a target guild owns
that guild's subscriptions, exceptions and notification channel. ADR 0001 anticipates
relaxing one of these — looking up why a user is listed should eventually be open to a
subscribing guild's own moderators — and asks that it be a change to one rule rather than
a hunt through handlers. So the rules are a table, and the handlers name an
:class:`Operation`. Narrowing pool management from the management guild's administrators
to a role of its own (ADR 0012) was that promise being kept: two lines of this table.

Both halves of the split are deliberate and they are not the same rule. Pool authority is
one role in one guild, because a listing bans people everywhere. Guild authority is
Administrator in the guild the request names, because a subscription only ever binds the
guild that holds it — so a guild's own administrators keep it, whatever the pool side is
configured to.

Two rules are only half Discord's answer, and are marked as such. `OWNER` names whoever
runs this deployment, which is not a fact Discord has at all (ADR 0011). `POOL_MANAGER`
asks Discord whether someone holds a role, but *which* role is configuration. Both sit
beside `MANAGEMENT_GUILD_ID`, and both only ever narrow.

The table is also read *before* anything is resolved. Each requirement says which single
fact about the caller has to be established, so a request that only needs the management
guild checked never pays for a scan of every guild Timothy is in.

Nothing here is async and nothing here touches Discord or the database. What it decides
is carried out by :mod:`timothy_api.deps`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from timothy_core.actors import Actor


class Operation(StrEnum):
    """Something a caller might be trying to do."""

    MANAGE_POOLS = "manage_pools"
    MANAGE_LISTINGS = "manage_listings"
    READ_POOLS = "read_pools"
    MANAGE_SUBSCRIPTIONS = "manage_subscriptions"
    MANAGE_EXCEPTIONS = "manage_exceptions"
    MANAGE_NOTIFICATION_CHANNEL = "manage_notification_channel"
    MANAGE_GUILD_ENFORCEMENT = "manage_guild_enforcement"
    READ_GUILD = "read_guild"
    LIST_GUILDS = "list_guilds"
    READ_AUDIT_LOG = "read_audit_log"
    READ_OPS = "read_ops"
    READ_ENFORCEMENT = "read_enforcement"
    REGISTER_GUILD = "register_guild"
    RELAY_EVENT = "relay_event"


class Requirement(StrEnum):
    """The one fact that has to be true for an operation to be allowed."""

    POOL_MANAGER = "pool_manager"
    """Holds one of `POOL_MANAGER_ROLE_IDS` in the management guild (ADR 0012).

    Administering the management guild is not this. The guild's administrators run a
    Discord server; pool managers decide who every subscribing guild bans, and that is a
    larger blast radius than "can edit channels here". An administrator who needs it
    grants themselves the role — they always can — and that grant is a deliberate,
    visible act rather than a side effect of a Discord permission.

    Unconfigured closes pool management for everybody, exactly as `OWNER` does for the
    operations view, and for the same reason: falling back to the administrators would
    silently undo the separation this exists to draw.
    """

    TARGET_GUILD_ADMIN = "target_guild_admin"
    """`ADMINISTRATOR` in the guild the request names."""

    ANY_GUILD_MEMBER = "any_guild_member"
    """Membership of some guild Timothy is in."""

    SYSTEM = "system"
    """Timothy acting on its own behalf. No Discord authority exists to check: these are
    the operations that follow from the bot joining or leaving a guild, where there is no
    human in the loop. The service token is the whole of the check."""

    OWNER = "owner"
    """Named in `TIMOTHY_OWNER_IDS` — whoever runs this deployment (ADR 0011).

    The only requirement here that is not a question for Discord, because "who operates
    this instance" is not a fact Discord has. It is deployment configuration, like
    `MANAGEMENT_GUILD_ID` beside it, and it only ever narrows: it gates one read-only
    view that would otherwise have been open to every administrator of the management
    guild.

    Costs no Discord call at all, which makes it the cheapest check in the table.
    """


REQUIREMENTS: Final[Mapping[Operation, Requirement]] = {
    Operation.MANAGE_POOLS: Requirement.POOL_MANAGER,
    Operation.MANAGE_LISTINGS: Requirement.POOL_MANAGER,
    # The audit log is mostly the record of the two operations above, so it follows them:
    # the people who can list a user are the people who review the listing.
    Operation.READ_AUDIT_LOG: Requirement.POOL_MANAGER,
    # The operator's view of Timothy itself: the queue, what is failing everywhere, what
    # the settings actually are. Administering the pool server does not make somebody the
    # person running the deployment, and this is the one screen where that distinction is
    # worth drawing (ADR 0011).
    Operation.READ_OPS: Requirement.OWNER,
    # ADR 0001's known future relaxation: to a subscribing guild's own moderators.
    Operation.READ_POOLS: Requirement.ANY_GUILD_MEMBER,
    Operation.MANAGE_SUBSCRIPTIONS: Requirement.TARGET_GUILD_ADMIN,
    Operation.MANAGE_EXCEPTIONS: Requirement.TARGET_GUILD_ADMIN,
    Operation.MANAGE_NOTIFICATION_CHANNEL: Requirement.TARGET_GUILD_ADMIN,
    Operation.MANAGE_GUILD_ENFORCEMENT: Requirement.TARGET_GUILD_ADMIN,
    Operation.READ_GUILD: Requirement.TARGET_GUILD_ADMIN,
    # The gate is only that the caller is somewhere Timothy is; the *answer* is filtered
    # to the guilds they administer, one resolved permission at a time. A rule saying
    # "administrator somewhere" would need resolving before it could be checked, which is
    # the same work as producing the list.
    Operation.LIST_GUILDS: Requirement.ANY_GUILD_MEMBER,
    Operation.READ_ENFORCEMENT: Requirement.TARGET_GUILD_ADMIN,
    Operation.REGISTER_GUILD: Requirement.SYSTEM,
    # A gateway event is something that happened, not something anyone asked for. There
    # is no human behind `GUILD_MEMBER_ADD` to derive authority from, and the exception
    # ADR 0006 may create from an unban is Timothy's own — which is exactly why it must
    # not go through the administrator-only exceptions route.
    Operation.RELAY_EVENT: Requirement.SYSTEM,
}


@dataclass(frozen=True, slots=True)
class PermissionContext:
    """What was resolved about the caller.

    Only the field the operation's requirement names is ever populated; the rest stay
    `False`, which is also what they mean when resolution found nothing.
    """

    actor: Actor
    pool_manager: bool = False
    target_guild_admin: bool = False
    any_guild_member: bool = False
    owner: bool = False


def requirement(operation: Operation) -> Requirement:
    """What has to be resolved before `operation` can be decided."""
    return REQUIREMENTS[operation]


def allows(operation: Operation, context: PermissionContext) -> bool:
    """Whether this caller may perform this operation.

    A system actor is refused everything except the operations that are its own. Timothy
    has no Discord permissions to derive authority from, so letting it stand in for a
    human would be an unaudited bypass of the whole model rather than an application of
    it.
    """
    needed = requirement(operation)
    if context.actor.is_system:
        return needed is Requirement.SYSTEM
    if needed is Requirement.SYSTEM:
        return False
    return {
        Requirement.POOL_MANAGER: context.pool_manager,
        Requirement.TARGET_GUILD_ADMIN: context.target_guild_admin,
        Requirement.ANY_GUILD_MEMBER: context.any_guild_member,
        Requirement.OWNER: context.owner,
    }[needed]
