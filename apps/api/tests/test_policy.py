"""The rules themselves, away from HTTP.

ADR 0001 asks for one policy module so that relaxing a rule is one edit. These tests
pin the table so the edit is deliberate.
"""

import pytest

from timothy_api.policy import (
    REQUIREMENTS,
    Operation,
    PermissionContext,
    Requirement,
    allows,
    requirement,
)
from timothy_core.actors import Actor

USER = Actor.user(1)
SYSTEM = Actor.system()


def test_every_operation_has_a_rule() -> None:
    """A route naming an operation with no entry would be a route with no check."""
    assert set(REQUIREMENTS) == set(Operation)


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (Operation.MANAGE_POOLS, Requirement.POOL_MANAGER),
        (Operation.MANAGE_LISTINGS, Requirement.POOL_MANAGER),
        (Operation.READ_AUDIT_LOG, Requirement.POOL_MANAGER),
        (Operation.READ_POOLS, Requirement.ANY_GUILD_MEMBER),
        (Operation.MANAGE_SUBSCRIPTIONS, Requirement.TARGET_GUILD_ADMIN),
        (Operation.MANAGE_EXCEPTIONS, Requirement.TARGET_GUILD_ADMIN),
        (Operation.MANAGE_NOTIFICATION_CHANNEL, Requirement.TARGET_GUILD_ADMIN),
        (Operation.MANAGE_GUILD_ENFORCEMENT, Requirement.TARGET_GUILD_ADMIN),
        (Operation.READ_GUILD, Requirement.TARGET_GUILD_ADMIN),
        (Operation.REGISTER_GUILD, Requirement.SYSTEM),
    ],
)
def test_the_authorization_table_matches_plan_md(
    operation: Operation, expected: Requirement
) -> None:
    assert requirement(operation) is expected


def test_the_pool_manager_role_owns_pools_and_nothing_else() -> None:
    """Including nothing in the management guild itself: the role is authority over the
    pools, not over the guild those pools happen to live in (ADR 0012)."""
    context = PermissionContext(actor=USER, pool_manager=True)

    assert allows(Operation.MANAGE_POOLS, context)
    assert allows(Operation.MANAGE_LISTINGS, context)
    assert allows(Operation.READ_AUDIT_LOG, context)
    assert not allows(Operation.MANAGE_SUBSCRIPTIONS, context)
    assert not allows(Operation.READ_POOLS, context)


def test_guild_admin_owns_that_guild_and_not_the_pools() -> None:
    context = PermissionContext(actor=USER, target_guild_admin=True)

    assert allows(Operation.MANAGE_SUBSCRIPTIONS, context)
    assert allows(Operation.MANAGE_EXCEPTIONS, context)
    assert allows(Operation.MANAGE_NOTIFICATION_CHANNEL, context)
    assert not allows(Operation.MANAGE_POOLS, context)


def test_membership_reads_but_does_not_write() -> None:
    context = PermissionContext(actor=USER, any_guild_member=True)

    assert allows(Operation.READ_POOLS, context)
    assert not allows(Operation.MANAGE_LISTINGS, context)
    assert not allows(Operation.READ_AUDIT_LOG, context)


def test_a_caller_with_nothing_resolved_is_refused_everything() -> None:
    context = PermissionContext(actor=USER)

    assert not any(allows(operation, context) for operation in Operation)


SYSTEMS_OWN = {
    Operation.REGISTER_GUILD,
    Operation.RELAY_EVENT,
    Operation.REPORT_GUILD_DIAGNOSTICS,
}
"""The things that follow from Discord telling Timothy something, rather than from anyone
asking: the bot joining or leaving a guild, a gateway event, and the bot reporting what
its own cache says about a guild's roles (ADR 0016)."""


def test_the_system_actor_may_only_do_its_own_work() -> None:
    """Timothy has no Discord permissions to derive authority from, so standing in for a
    human would be a bypass of the model rather than an application of it."""
    context = PermissionContext(actor=SYSTEM)

    assert all(allows(operation, context) for operation in SYSTEMS_OWN)
    assert not any(
        allows(operation, context) for operation in Operation if operation not in SYSTEMS_OWN
    )


def test_a_human_may_not_do_the_system_s_work() -> None:
    """Even a pool manager: guild registration follows the bot joining, and a human
    asserting it would be asserting something untrue."""
    context = PermissionContext(
        actor=USER, pool_manager=True, target_guild_admin=True, any_guild_member=True
    )

    assert not allows(Operation.REGISTER_GUILD, context)
