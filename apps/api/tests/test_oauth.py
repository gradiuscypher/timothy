"""The real OAuth client, against a Discord made of canned responses.

:class:`~apps.api.tests.conftest.FakeOAuth` stands in for this everywhere else, which
means the code that actually parses Discord's JSON has no other cover. What is tested
here is the parsing and the failure handling — the three requests, in order, and every
way they can come back wrong.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from timothy_api.oauth import (
    API_BASE,
    SCOPES,
    DiscordOAuth,
    OAuthError,
    redirect_uri,
)
from timothy_api.settings import Settings

pytestmark = pytest.mark.anyio

USER_ID = 242_024_455_190_577_152
GUILD_ID = 100_000_000_000_000_002


Handler = Callable[[httpx.Request], httpx.Response]


def _oauth(handler: Handler) -> DiscordOAuth:
    return DiscordOAuth(
        client_id="client",
        client_secret="secret",
        redirect_uri="https://timothy.example.com/api/auth/callback",
        client=httpx.AsyncClient(base_url=API_BASE, transport=httpx.MockTransport(handler)),
    )


def _discord(
    *,
    token: dict[str, Any] | None = None,
    user: object = None,
    guilds: object = None,
    status: int = 200,
) -> Handler:
    """A Discord that answers the three calls the flow makes."""
    bodies = {
        "/api/v10/oauth2/token": token if token is not None else {"access_token": "at"},
        "/api/v10/users/@me": user
        if user is not None
        else {"id": str(USER_ID), "username": "gradius", "avatar": "abc"},
        "/api/v10/users/@me/guilds": guilds if guilds is not None else [{"id": str(GUILD_ID)}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        body = bodies[request.url.path]
        return httpx.Response(status, content=json.dumps(body))

    return handle


async def test_it_identifies_the_person_who_consented() -> None:
    identity = await _oauth(_discord()).identify(code="the-code")

    assert identity.user_id == USER_ID
    assert identity.username == "gradius"
    assert identity.avatar == "abc"
    assert identity.guild_ids == (GUILD_ID,)


async def test_the_display_name_wins_over_the_handle() -> None:
    """Discord shows `global_name` everywhere a person is named, so Timothy should too."""
    identity = await _oauth(
        _discord(user={"id": str(USER_ID), "username": "gradius", "global_name": "Tim"})
    ).identify(code="c")

    assert identity.username == "Tim"


async def test_a_user_with_no_avatar_has_none_rather_than_a_string() -> None:
    identity = await _oauth(
        _discord(user={"id": str(USER_ID), "username": "g", "avatar": None})
    ).identify(code="c")

    assert identity.avatar is None


async def test_guilds_that_are_not_objects_with_ids_are_ignored() -> None:
    """Anything unrecognised is dropped rather than raised over: a guild Timothy cannot
    parse is a guild it will not narrow a scan with, which fails towards asking Discord."""
    identity = await _oauth(
        _discord(guilds=[{"id": str(GUILD_ID)}, {"name": "no id"}, "nonsense", 7])
    ).identify(code="c")

    assert identity.guild_ids == (GUILD_ID,)


async def test_a_guild_list_that_is_not_a_list_is_no_guilds() -> None:
    identity = await _oauth(_discord(guilds={"message": "401: Unauthorized"})).identify(
        code="c"
    )

    assert identity.guild_ids == ()


async def test_a_refused_exchange_is_an_oauth_error() -> None:
    with pytest.raises(OAuthError, match="401"):
        await _oauth(_discord(status=401)).identify(code="stale")


async def test_an_exchange_with_no_token_in_it_is_an_oauth_error() -> None:
    with pytest.raises(OAuthError, match="no access token"):
        await _oauth(_discord(token={"error": "invalid_grant"})).identify(code="c")


async def test_an_identity_with_no_id_is_an_oauth_error() -> None:
    with pytest.raises(OAuthError, match="no usable id"):
        await _oauth(_discord(user={"username": "nobody"})).identify(code="c")


async def test_an_answer_that_is_not_an_object_is_an_oauth_error() -> None:
    with pytest.raises(OAuthError, match="not an object"):
        await _oauth(_discord(user=["not", "an", "object"])).identify(code="c")


async def test_an_answer_that_is_not_json_is_an_oauth_error() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>maintenance</html>")

    with pytest.raises(OAuthError, match="not JSON"):
        await _oauth(handle).identify(code="c")


async def test_discord_being_unreachable_is_an_oauth_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        unreachable = httpx.ConnectError("no route to host", request=request)
        raise unreachable

    with pytest.raises(OAuthError, match="could not reach Discord"):
        await _oauth(handle).identify(code="c")


async def test_discord_going_away_mid_flow_is_an_oauth_error() -> None:
    """The exchange can succeed and the identify call still fail — three requests, three
    chances for the network to end."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v10/oauth2/token":
            return _discord()(request)
        unreachable = httpx.ReadTimeout("timed out", request=request)
        raise unreachable

    with pytest.raises(OAuthError, match="could not reach Discord"):
        await _oauth(handle).identify(code="c")


async def test_the_error_never_repeats_what_discord_said() -> None:
    """The token exchange carries the client secret in its body, and Discord's own error
    responses have been known to echo the request back."""
    with pytest.raises(OAuthError) as refused:
        await _oauth(_discord(token={"echo": "secret"}, status=400)).identify(code="c")

    assert "secret" not in str(refused.value)


async def test_it_uses_the_access_token_and_stores_nothing() -> None:
    """Timothy never acts as the user, so a stored token would be a credential with
    nothing to do and somewhere to leak from."""
    seen: list[tuple[str, str | None]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization")))
        return _discord()(request)

    await _oauth(handle).identify(code="c")

    assert seen == [
        ("/api/v10/oauth2/token", None),
        ("/api/v10/users/@me", "Bearer at"),
        ("/api/v10/users/@me/guilds", "Bearer at"),
    ]


async def test_it_closes_its_connection_pool() -> None:
    flow = _oauth(_discord())
    await flow.identify(code="c")

    await flow.close()


def test_the_authorize_url_carries_the_scopes_and_the_state() -> None:
    url = _oauth(_discord()).authorize_url(state="opaque")

    assert "client_id=client" in url
    assert "state=opaque" in url
    assert "scope=identify+guilds" in url
    assert SCOPES == "identify guilds"


async def test_it_is_unconfigured_until_all_three_settings_are_set() -> None:
    """Any one of them missing means a login that cannot complete, and a login that
    cannot complete should not start."""
    settings = Settings(
        internal_token="t",
        discord_client_id="client",
        discord_client_secret="secret",
        public_base_url="https://timothy.example.com",
    )
    missing = [
        {},
        {"public_base_url": ""},
        {"discord_client_id": ""},
        {"discord_client_secret": SecretStr("")},
    ]

    configured = []
    for update in missing:
        flow = DiscordOAuth.create(settings.model_copy(update=update))
        configured.append(flow.configured)
        await flow.close()

    assert configured == [True, False, False, False]
    assert redirect_uri(settings).endswith("/api/auth/callback")
