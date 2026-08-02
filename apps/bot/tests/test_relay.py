"""The gateway events, on their way to the backend.

Two things are being pinned. That each event reaches the right route as `system` — a
relay that spoke for a user would be refused, because `Requirement.SYSTEM` and a human's
authority are mutually exclusive. And that a backend in trouble never takes the gateway
connection down with it: the events lost while it is failing are exactly what the sweep
exists to catch, and a bot that crashed on the first 503 would lose every one after it
too.
"""

import logging
from collections.abc import Awaitable, Callable

import pytest
from support import GUILD, LISTED_USER, Backend

from timothy_bot import relay
from timothy_bot.api import SYSTEM, Api

pytestmark = pytest.mark.anyio


async def test_a_member_joining_is_relayed(api: Api, backend: Backend) -> None:
    backend.replies(202, {"action": "enforcement queued"})

    await relay.member_joined(api, guild_id=GUILD, user_id=LISTED_USER)

    assert backend.called == ("POST", "/events/member-join")
    assert backend.sent == {"guild_id": str(GUILD), "user_id": str(LISTED_USER)}
    assert backend.request.headers["X-Timothy-Actor"] == SYSTEM


async def test_what_the_backend_decided_is_logged(
    api: Api, backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    """How an operator sees whether the automatic exception fired, or was suppressed as
    Timothy's own revert."""
    backend.replies(202, {"action": "ignored: Timothy's own revert"})

    with caplog.at_level(logging.INFO, logger="timothy_bot.relay"):
        await relay.ban_removed(api, guild_id=GUILD, user_id=LISTED_USER)

    assert "ignored: Timothy's own revert" in caplog.text


async def test_a_ban_being_lifted_is_relayed(api: Api, backend: Backend) -> None:
    backend.replies(202, {"action": "exception created"})

    await relay.ban_removed(api, guild_id=GUILD, user_id=LISTED_USER)

    assert backend.called == ("POST", "/events/ban-remove")


async def test_joining_a_guild_registers_it(api: Api, backend: Backend) -> None:
    await relay.guild_joined(api, GUILD)

    assert backend.called == ("PUT", f"/guilds/{GUILD}")


async def test_being_removed_from_a_guild_deregisters_it(api: Api, backend: Backend) -> None:
    backend.replies(204)

    await relay.guild_left(api, GUILD)

    assert backend.called == ("DELETE", f"/guilds/{GUILD}")


async def test_connecting_re_announces_every_guild(api: Api, backend: Backend) -> None:
    """Registration is idempotent, so this is safe on every reconnect — and only the
    first one auto-subscribes, so a guild that has unsubscribed stays unsubscribed."""
    other = GUILD + 1

    await relay.announce_guilds(api, [GUILD, other])

    assert [request.url.path for request in backend.requests] == [
        f"/guilds/{GUILD}",
        f"/guilds/{other}",
    ]


@pytest.mark.parametrize(
    "relayed",
    [
        lambda api: relay.member_joined(api, guild_id=GUILD, user_id=LISTED_USER),
        lambda api: relay.ban_removed(api, guild_id=GUILD, user_id=LISTED_USER),
        lambda api: relay.guild_joined(api, GUILD),
        lambda api: relay.guild_left(api, GUILD),
    ],
)
async def test_a_failing_backend_does_not_take_the_gateway_down(
    api: Api,
    backend: Backend,
    caplog: pytest.LogCaptureFixture,
    relayed: Callable[[Api], Awaitable[None]],
) -> None:
    backend.fails(503, "Discord is unreachable")

    with caplog.at_level(logging.WARNING, logger="timothy_bot.relay"):
        await relayed(api)

    assert "Discord is unreachable" in caplog.text


async def test_one_guild_failing_to_register_does_not_stop_the_rest(
    api: Api, backend: Backend
) -> None:
    backend.fails(503, "Discord is unreachable")

    await relay.announce_guilds(api, [GUILD, GUILD + 1])

    assert len(backend.requests) == 2
