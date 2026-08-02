"""Who may do what. The only place that answers it (ADR 0001).

Authority is *derived* from Discord rather than stored: holding `ADMINISTRATOR` in the
management guild owns pools and listings, holding it in a target guild owns that guild's
subscriptions, exceptions and notification channel. ADR 0001 anticipates relaxing one of
these — looking up why a user is listed should eventually be open to a subscribing
guild's own moderators — and asks that it be a change to one rule rather than a hunt
through handlers. So the rules are a table, and the handlers name an :class:`Operation`.

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
    READ_AUDIT_LOG = "read_audit_log"
    READ_ENFORCEMENT = "read_enforcement"
    REGISTER_GUILD = "register_guild"
    RELAY_EVENT = "relay_event"


class Requirement(StrEnum):
    """The one fact that has to be true for an operation to be allowed."""

    MANAGEMENT_ADMIN = "management_admin"
    """`ADMINISTRATOR` in the management guild."""

    TARGET_GUILD_ADMIN = "target_guild_admin"
    """`ADMINISTRATOR` in the guild the request names."""

    ANY_GUILD_MEMBER = "any_guild_member"
    """Membership of some guild Timothy is in."""

    SYSTEM = "system"
    """Timothy acting on its own behalf. No Discord authority exists to check: these are
    the operations that follow from the bot joining or leaving a guild, where there is no
    human in the loop. The service token is the whole of the check."""


REQUIREMENTS: Final[Mapping[Operation, Requirement]] = {
    Operation.MANAGE_POOLS: Requirement.MANAGEMENT_ADMIN,
    Operation.MANAGE_LISTINGS: Requirement.MANAGEMENT_ADMIN,
    Operation.READ_AUDIT_LOG: Requirement.MANAGEMENT_ADMIN,
    # ADR 0001's known future relaxation: to a subscribing guild's own moderators.
    Operation.READ_POOLS: Requirement.ANY_GUILD_MEMBER,
    Operation.MANAGE_SUBSCRIPTIONS: Requirement.TARGET_GUILD_ADMIN,
    Operation.MANAGE_EXCEPTIONS: Requirement.TARGET_GUILD_ADMIN,
    Operation.MANAGE_NOTIFICATION_CHANNEL: Requirement.TARGET_GUILD_ADMIN,
    Operation.MANAGE_GUILD_ENFORCEMENT: Requirement.TARGET_GUILD_ADMIN,
    Operation.READ_GUILD: Requirement.TARGET_GUILD_ADMIN,
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
    management_admin: bool = False
    target_guild_admin: bool = False
    any_guild_member: bool = False


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
        Requirement.MANAGEMENT_ADMIN: context.management_admin,
        Requirement.TARGET_GUILD_ADMIN: context.target_guild_admin,
        Requirement.ANY_GUILD_MEMBER: context.any_guild_member,
    }[needed]
