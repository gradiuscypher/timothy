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
        (Operation.MANAGE_POOLS, Requirement.MANAGEMENT_ADMIN),
        (Operation.MANAGE_LISTINGS, Requirement.MANAGEMENT_ADMIN),
        (Operation.READ_AUDIT_LOG, Requirement.MANAGEMENT_ADMIN),
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


def test_management_admin_owns_pools_and_nothing_else() -> None:
    context = PermissionContext(actor=USER, management_admin=True)

    assert allows(Operation.MANAGE_POOLS, context)
    assert allows(Operation.MANAGE_LISTINGS, context)
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


def test_the_system_actor_may_only_do_its_own_work() -> None:
    """Timothy has no Discord permissions to derive authority from, so standing in for a
    human would be a bypass of the model rather than an application of it."""
    context = PermissionContext(actor=SYSTEM)

    assert allows(Operation.REGISTER_GUILD, context)
    assert not any(
        allows(operation, context)
        for operation in Operation
        if operation is not Operation.REGISTER_GUILD
    )


def test_a_human_may_not_do_the_system_s_work() -> None:
    """Even a management administrator: guild registration follows the bot joining, and
    a human asserting it would be asserting something untrue."""
    context = PermissionContext(
        actor=USER, management_admin=True, target_guild_admin=True, any_guild_member=True
    )

    assert not allows(Operation.REGISTER_GUILD, context)
