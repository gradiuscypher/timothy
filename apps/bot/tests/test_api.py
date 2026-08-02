"""What the bot sends, and what it makes of the answer."""

import httpx
import pytest
from support import GUILD, LISTED_USER, MODERATOR, Backend

from timothy_bot.api import SYSTEM, Api, ApiError, create_client


@pytest.mark.anyio
async def test_every_call_carries_the_internal_token(api: Api, backend: Backend) -> None:
    await api.list_pools()

    assert backend.request.headers["Authorization"] == "Bearer internal-token-for-tests"


@pytest.mark.anyio
async def test_a_command_acts_for_the_moderator_who_typed_it(
    api: Api, backend: Backend
) -> None:
    """Identity, never authority. The backend resolves what this user may do itself."""
    await api.as_user(MODERATOR).list_pools()

    assert backend.request.headers["X-Timothy-Actor"] == f"user:{MODERATOR}"


@pytest.mark.anyio
async def test_a_command_names_the_guild_it_was_typed_in(api: Api, backend: Backend) -> None:
    """Still identity, not authority. The backend resolves membership against Discord
    either way; this only tells it which guild to look in first, which is the difference
    between one call and a hundred for the one permission that has to scan them all."""
    await api.as_user(MODERATOR, from_guild=GUILD).list_pools()

    assert backend.request.headers["X-Timothy-From-Guild"] == str(GUILD)


@pytest.mark.anyio
async def test_a_call_from_nowhere_names_no_guild(api: Api, backend: Backend) -> None:
    """The relay has no interaction behind it, so there is no guild to name and the
    header is simply absent rather than empty."""
    await api.as_user(MODERATOR).list_pools()

    assert "X-Timothy-From-Guild" not in backend.request.headers


@pytest.mark.anyio
async def test_the_relay_acts_as_the_system(api: Api, backend: Backend) -> None:
    """`Requirement.SYSTEM` is refused everything a human owns, and the reverse — so
    relaying an event as a user, or a command as the system, would be rejected."""
    backend.replies(202, {"action": "enforcement queued"})

    await api.member_joined(guild_id=GUILD, user_id=LISTED_USER)

    assert backend.request.headers["X-Timothy-Actor"] == SYSTEM


@pytest.mark.anyio
async def test_a_pool_name_cannot_address_another_route(api: Api, backend: Backend) -> None:
    """Pool names are typed by moderators. A pool called `spam/ham` is one path segment."""
    await api.delete_pool("spam/ham")

    assert backend.path == "/pools/spam%2Fham"


@pytest.mark.anyio
async def test_a_pool_name_with_a_space_survives_the_round_trip(
    api: Api, backend: Backend
) -> None:
    await api.delete_pool("known trolls")

    assert backend.path == "/pools/known%20trolls"


@pytest.mark.anyio
async def test_snowflakes_go_as_strings(api: Api, backend: Backend) -> None:
    """They are 64-bit, and the web UI's JSON parser is not. The API's own schema says
    strings, so the bot sends strings."""
    backend.replies(201, {"id": 1})

    await api.create_listing(pool_name="spam", user_id=LISTED_USER, reason="raiding")

    assert backend.sent == {"user_id": str(LISTED_USER), "reason": "raiding"}


@pytest.mark.anyio
async def test_a_no_content_answer_is_not_parsed_as_json(api: Api, backend: Backend) -> None:
    """A 204 has no body, and asking httpx for one raises."""
    backend.replies(204)

    assert await api.delete_pool("spam") is None


@pytest.mark.anyio
async def test_a_refusal_carries_the_backend_s_own_explanation(
    api: Api, backend: Backend
) -> None:
    """Which is what the moderator ends up reading: "no such pool: spma" beats "404"."""
    backend.fails(404, "no such pool: spma")

    with pytest.raises(ApiError) as raised:
        await api.delete_pool("spma")

    assert raised.value.detail == "no such pool: spma"
    assert raised.value.status_code == 404


@pytest.mark.anyio
async def test_a_refusal_without_a_detail_still_says_something(
    api: Api, backend: Backend
) -> None:
    backend.replies(502, ["not the shape anyone expected"])

    with pytest.raises(ApiError, match="502"):
        await api.list_pools()


@pytest.mark.anyio
async def test_a_body_that_is_not_json_still_says_something(
    http: httpx.AsyncClient, backend: Backend
) -> None:
    backend.answers_with(httpx.Response(500, text="<html>nginx</html>"))

    with pytest.raises(ApiError, match="500"):
        await Api(http, actor=SYSTEM).list_pools()


@pytest.mark.anyio
async def test_a_backend_that_cannot_be_reached_is_an_api_error() -> None:
    """Not an httpx exception. Everything above this catches one type, and a handler that
    let a transport error through would leave the interaction unanswered."""

    def refuse(_request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message)

    async with httpx.AsyncClient(
        base_url="http://backend:8000", transport=httpx.MockTransport(refuse)
    ) as client:
        with pytest.raises(ApiError) as raised:
            await Api(client, actor=SYSTEM).list_pools()

    assert raised.value.status_code is None
    assert "could not reach" in raised.value.detail


@pytest.mark.anyio
async def test_the_event_relay_reports_what_the_backend_decided(
    api: Api, backend: Backend
) -> None:
    """The one-line `action` is the whole point of the 202 — it is how an operator sees
    whether an auto-exception fired."""
    backend.replies(202, {"action": "ignored: Timothy's own revert"})

    action = await api.ban_removed(guild_id=GUILD, user_id=LISTED_USER)

    assert action == "ignored: Timothy's own revert"
    assert backend.sent == {"guild_id": str(GUILD), "user_id": str(LISTED_USER)}


@pytest.mark.anyio
async def test_the_client_waits_less_than_discord_does() -> None:
    """Three seconds is the interaction deadline; a slower answer is unusable anyway."""
    async with create_client(base_url="http://backend:8000", token="t", timeout=2.5) as client:
        assert client.timeout.read == 2.5
        assert client.headers["Authorization"] == "Bearer t"
