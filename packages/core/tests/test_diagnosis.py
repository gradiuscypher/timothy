"""Discord's two hierarchy rules, case by case.

Both are rules people get wrong from the Discord UI alone, which is the whole reason
this module exists rather than the web UI comparing numbers itself.
"""

from timothy_core.enforcement.diagnosis import (
    BanBlocker,
    Role,
    Standing,
    blocking_roles,
    diagnose,
    unbannable_roles,
)

OWNER = 900
USER = 200

# Timothy sits at 5. `EQUAL` is the interesting one: Discord's own role list shows it
# level with Timothy, which reads as fine and is not.
BELOW = Role(role_id=1, name="member", position=2, member_count=4000)
EQUAL = Role(role_id=2, name="moderator", position=5, member_count=12)
ABOVE = Role(role_id=3, name="admin", position=9, member_count=3)
INTEGRATION = Role(role_id=4, name="Nitro Booster", position=7, member_count=8, managed=True)


def _standing(*, can_ban: bool = True, roles: tuple[Role, ...] = ()) -> Standing:
    return Standing(can_ban=can_ban, top_role_position=5, owner_id=OWNER, roles=roles)


# -- which roles are out of reach --------------------------------------------


def test_a_role_level_with_timothy_is_unbannable() -> None:
    """Strict inequality. This is the off-by-one the whole feature is here to catch."""
    assert unbannable_roles(_standing(roles=(EQUAL,))) == (EQUAL,)


def test_a_role_below_timothy_is_bannable() -> None:
    assert unbannable_roles(_standing(roles=(BELOW,))) == ()


def test_unbannable_roles_come_back_highest_first() -> None:
    standing = _standing(roles=(EQUAL, BELOW, ABOVE, INTEGRATION))

    assert unbannable_roles(standing) == (ABOVE, INTEGRATION, EQUAL)


def test_a_managed_role_is_still_unbannable() -> None:
    """It is reported, and flagged, so the UI can say the usual fix does not apply."""
    (role,) = unbannable_roles(_standing(roles=(INTEGRATION,)))

    assert role.managed


def test_no_ban_permission_does_not_make_every_role_unbannable() -> None:
    """A true answer that hides the hierarchy problem. The banner covers this case."""
    assert unbannable_roles(_standing(can_ban=False, roles=(BELOW,))) == ()


def test_a_role_the_guild_no_longer_has_is_dropped() -> None:
    """The snapshot and the member lookup are taken at different moments."""
    assert blocking_roles(_standing(roles=(ABOVE,)), frozenset({ABOVE.role_id, 999})) == (
        ABOVE,
    )


# -- explaining one failure --------------------------------------------------


def test_missing_ban_permission_subsumes_everything() -> None:
    """Even a target holding nothing at all. There is nothing else worth saying."""
    result = diagnose(
        standing=_standing(can_ban=False, roles=(ABOVE,)),
        user_id=USER,
        role_ids=frozenset({ABOVE.role_id}),
    )

    assert result.blocker is BanBlocker.NO_BAN_PERMISSION
    assert result.blocking_roles == ()


def test_the_guild_owner_is_out_of_reach() -> None:
    result = diagnose(standing=_standing(), user_id=OWNER, role_ids=frozenset())

    assert result.blocker is BanBlocker.GUILD_OWNER


def test_an_outranked_target_names_only_the_roles_that_block() -> None:
    result = diagnose(
        standing=_standing(roles=(BELOW, EQUAL, ABOVE)),
        user_id=USER,
        role_ids=frozenset({BELOW.role_id, EQUAL.role_id, ABOVE.role_id}),
    )

    assert result.blocker is BanBlocker.OUTRANKED
    assert result.blocking_roles == (ABOVE, EQUAL)


def test_a_target_who_only_holds_lower_roles_is_not_outranked() -> None:
    result = diagnose(
        standing=_standing(roles=(BELOW,)),
        user_id=USER,
        role_ids=frozenset({BELOW.role_id}),
        detail="403 Forbidden",
    )

    assert result.blocker is BanBlocker.UNKNOWN
    assert result.detail == "403 Forbidden"


def test_a_target_who_is_no_longer_here_is_history() -> None:
    """`None` roles is what a member lookup finding nobody means."""
    result = diagnose(standing=_standing(roles=(ABOVE,)), user_id=USER, role_ids=None)

    assert result.blocker is BanBlocker.LEFT_GUILD


def test_a_lookup_that_failed_is_not_a_target_who_left() -> None:
    """Both produce no roles. Collapsing them would tell an administrator the problem had
    gone away at the moment Timothy stopped being able to see it."""
    result = diagnose(
        standing=_standing(roles=(ABOVE,)), user_id=USER, role_ids=None, lookup_failed=True
    )

    assert result.blocker is BanBlocker.UNKNOWN


def test_a_lookup_that_failed_still_reports_what_the_snapshot_knows() -> None:
    result = diagnose(
        standing=_standing(can_ban=False), user_id=USER, role_ids=None, lookup_failed=True
    )

    assert result.blocker is BanBlocker.NO_BAN_PERMISSION


def test_discords_own_words_survive_every_verdict() -> None:
    """The stored error is what an operator falls back on when the model has no answer."""
    said = "Missing Permissions"
    for role_ids in (None, frozenset(), frozenset({ABOVE.role_id})):
        result = diagnose(
            standing=_standing(roles=(ABOVE,)),
            user_id=USER,
            role_ids=role_ids,
            detail=said,
        )

        assert result.detail == said
