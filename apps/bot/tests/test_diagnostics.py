"""Reading a guild's shape out of the gateway's own cache.

The three cases that matter are all about honesty rather than arithmetic: `@everyone`
must not be reported, an unchunked guild must not report zeroes, and a guild Discord has
not finished sending must not report at all.
"""

import json
from typing import Any

import pytest
from support import GUILD, Backend

from timothy_bot import diagnostics
from timothy_bot.api import Api

TIMOTHY_ROLE = 600_000_000_000_000_001
ADMIN_ROLE = 600_000_000_000_000_002
BOOSTER_ROLE = 600_000_000_000_000_003
OWNER = 200_000_000_000_000_009


class StubRole:
    """One of discord.py's roles, reduced to what the snapshot reads."""

    def __init__(
        self,
        role_id: int,
        name: str,
        position: int,
        *,
        members: int = 0,
        managed: bool = False,
    ) -> None:
        self.id = role_id
        self.name = name
        self.position = position
        self.members = [object()] * members
        self.managed = managed


class StubPermissions:
    def __init__(self, *, ban_members: bool, administrator: bool = False) -> None:
        self.ban_members = ban_members
        self.administrator = administrator


class StubMember:
    def __init__(self, top_role: StubRole, permissions: StubPermissions) -> None:
        self.top_role = top_role
        self.guild_permissions = permissions


class StubGuild:
    """A guild as the gateway holds it. Nothing here makes a Discord call."""

    def __init__(
        self,
        *,
        chunked: bool = True,
        me: StubMember | None = None,
        available: bool = True,
        owner_id: int | None = OWNER,
        roles: list[StubRole] | None = None,
    ) -> None:
        self.id = GUILD
        self.chunked = chunked
        self.owner_id = owner_id
        self.name = "a guild"
        timothy = StubRole(TIMOTHY_ROLE, "Timothy", 5)
        # `available=False` is a guild Discord has started sending and not finished, which
        # is distinct from one where a test simply did not name Timothy's own member.
        default = StubMember(timothy, StubPermissions(ban_members=True))
        self.me = (me if me is not None else default) if available else None
        self.roles = (
            roles
            if roles is not None
            else [
                # `@everyone`'s ID is the guild's own — the one role that must not be
                # reported, because every member holds it.
                StubRole(GUILD, "@everyone", 0, members=4000),
                timothy,
                StubRole(ADMIN_ROLE, "admin", 9, members=3),
                StubRole(BOOSTER_ROLE, "Nitro Booster", 7, members=8, managed=True),
            ]
        )


def _snapshot(**kwargs: object) -> dict[str, Any]:
    result = diagnostics.snapshot(StubGuild(**kwargs))  # ty: ignore[invalid-argument-type]
    assert result is not None
    return result


def test_everyone_is_never_reported() -> None:
    """Its ID is the guild's. Reported, it would sit in the hierarchy list forever as a
    role every single member holds."""
    names = [role["name"] for role in _snapshot()["roles"]]

    assert "@everyone" not in names


def test_roles_carry_their_position_and_count() -> None:
    (admin,) = [role for role in _snapshot()["roles"] if role["name"] == "admin"]

    assert admin["position"] == 9
    assert admin["member_count"] == 3
    assert admin["role_id"] == str(ADMIN_ROLE)


def test_an_integrations_role_is_flagged() -> None:
    """ "Move Timothy above it" is the only advice that applies to one."""
    (booster,) = [role for role in _snapshot()["roles"] if role["managed"]]

    assert booster["name"] == "Nitro Booster"


def test_an_unchunked_guild_counts_nobody_rather_than_everybody() -> None:
    """A zero from a half-filled cache reads as "nobody is affected", which is the one
    wrong answer worse than no answer at all."""
    report = _snapshot(chunked=False)

    assert report["member_counts_complete"] is False
    assert all(role["member_count"] is None for role in report["roles"])


def test_timothys_own_standing_comes_from_resolved_permissions() -> None:
    report = _snapshot()

    assert report["can_ban"] is True
    assert report["top_role_position"] == 5
    assert report["top_role_name"] == "Timothy"
    assert report["owner_id"] == str(OWNER)


def test_a_guild_without_a_ban_permission_says_so() -> None:
    guild = StubGuild(
        me=StubMember(StubRole(TIMOTHY_ROLE, "Timothy", 5), StubPermissions(ban_members=False))
    )

    report = diagnostics.snapshot(guild)  # ty: ignore[invalid-argument-type]

    assert report is not None
    assert report["can_ban"] is False


def test_a_guild_discord_has_not_finished_sending_is_not_reported() -> None:
    """During an outage a guild arrives without Timothy's own member object. Reporting
    `can_ban: false` for it would put a red banner in front of an administrator who has
    done nothing wrong."""
    assert diagnostics.snapshot(StubGuild(available=False)) is None  # ty: ignore[invalid-argument-type]
    assert diagnostics.snapshot(StubGuild(owner_id=None)) is None  # ty: ignore[invalid-argument-type]


# -- talking to the backend --------------------------------------------------


@pytest.mark.anyio
async def test_reporting_puts_the_snapshot(api: Api, backend: Backend) -> None:
    reported = await diagnostics.report(api, StubGuild())  # ty: ignore[invalid-argument-type]

    assert reported is True
    (request,) = backend.requests
    assert request.method == "PUT"
    assert request.url.path == f"/guilds/{GUILD}/diagnostics"
    assert json.loads(request.content)["can_ban"] is True


@pytest.mark.anyio
async def test_a_backend_that_is_down_does_not_take_the_gateway_with_it(
    api: Api, backend: Backend
) -> None:
    """The sweep and the next round are both safety nets for exactly this."""
    backend.fails(503, "unavailable")

    assert await diagnostics.report(api, StubGuild()) is False  # ty: ignore[invalid-argument-type]


@pytest.mark.anyio
async def test_an_unreportable_guild_is_never_sent(api: Api, backend: Backend) -> None:
    assert await diagnostics.report(api, StubGuild(available=False)) is False  # ty: ignore[invalid-argument-type]
    assert backend.requests == []


@pytest.mark.anyio
async def test_collecting_requests_reads_the_pending_queue(api: Api, backend: Backend) -> None:
    backend.replies(body={"guild_ids": [str(GUILD)]})

    wanted = await diagnostics.requested(api)

    assert wanted == frozenset({GUILD})
    assert backend.requests[0].url.path == "/diagnostics/pending"


@pytest.mark.anyio
async def test_a_backend_that_will_not_answer_means_nobody_asked(
    api: Api, backend: Backend
) -> None:
    """The scheduled round covers every guild regardless, so this delays a refresh rather
    than losing a guild."""
    backend.fails(503, "unavailable")

    assert await diagnostics.requested(api) == frozenset()
