"""Logging in, and what a browser is afterwards.

The API had one kind of caller until now: a container holding a shared secret. A browser
is the second, and the two must not be able to become each other — that is what most of
this file is about.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text

from timothy_api import sessions
from timothy_api.app import create_app
from timothy_api.oauth import redirect_uri
from timothy_api.settings import Settings
from timothy_core.migrations import sync_url
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    MANAGEMENT_GUILD,
    MEMBER,
    OUTSIDER,
    POOL_ADMIN,
    FakeOAuth,
    headers,
    sign_in,
)

ORIGIN = {"Origin": "http://testserver"}


def _expire(settings: Settings) -> None:
    """Age every session out, without waiting for a week to pass."""
    engine = create_engine(sync_url(settings.database_url))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE sessions SET expires_at = :past"),
                # The text form SQLAlchemy's DateTime writes. Handing sqlite3 a
                # `datetime` uses its deprecated default adapter instead.
                {
                    "past": (datetime.now(UTC) - timedelta(days=1))
                    .replace(tzinfo=None)
                    .isoformat(sep=" ")
                },
            )
    finally:
        engine.dispose()


# -- the flow --------------------------------------------------------------------------


def test_login_sends_the_browser_to_discord_with_a_state(client: TestClient) -> None:
    response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 307
    state = client.cookies[sessions.STATE_COOKIE_NAME]
    assert state
    assert f"state={state}" in response.headers["location"]


def test_login_is_closed_when_the_application_is_not_configured_for_it(
    client: TestClient, oauth: FakeOAuth
) -> None:
    """Fails closed, and says which settings are missing. Redirecting to Discord with an
    empty client ID would make this Discord's error message about Discord's problem."""
    oauth.configured = False

    response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 503
    assert "TIMOTHY_DISCORD_CLIENT_ID" in response.json()["detail"]


def test_the_callback_issues_a_session(client: TestClient, oauth: FakeOAuth) -> None:
    sign_in(client, oauth, user_id=GUILD_ADMIN)

    me = client.get("/auth/me").json()

    assert me["user_id"] == str(GUILD_ADMIN)
    assert me["actor"] == f"user:{GUILD_ADMIN}"
    assert me["username"] == "mod"


def test_the_callback_refuses_a_state_it_did_not_issue(
    client: TestClient, oauth: FakeOAuth
) -> None:
    """Without this, somebody can hand a victim's browser their own authorization code
    and have it silently log in to the attacker's Discord account."""
    client.get("/auth/login", follow_redirects=False)
    oauth.register("code", user_id=GUILD_ADMIN, guild_ids=(GUILD,))

    response = client.get("/auth/callback?code=code&state=somebody-elses")

    assert response.status_code == 400
    assert sessions.COOKIE_NAME not in client.cookies


def test_the_callback_refuses_a_state_with_no_cookie_behind_it(
    client: TestClient, oauth: FakeOAuth
) -> None:
    """A callback arriving without ever having been to /login is not a login."""
    oauth.register("code", user_id=GUILD_ADMIN, guild_ids=(GUILD,))

    response = client.get("/auth/callback?code=code&state=invented")

    assert response.status_code == 400


def test_a_refused_exchange_lands_on_a_page_that_can_say_so(
    client: TestClient, oauth: FakeOAuth
) -> None:
    """This URL is somewhere a browser lands, so a failure has to end up somewhere a
    person can read. JSON here would be a wall of text in the address bar."""
    client.get("/auth/login", follow_redirects=False)
    state = client.cookies[sessions.STATE_COOKIE_NAME]
    oauth.fails = True

    response = client.get(f"/auth/callback?code=code&state={state}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?login=failed"
    assert sessions.COOKIE_NAME not in client.cookies


def test_logging_out_ends_the_session(client: TestClient, oauth: FakeOAuth) -> None:
    sign_in(client, oauth)

    assert client.post("/auth/logout", headers=ORIGIN).status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_a_session_survives_only_until_it_expires(
    client: TestClient, oauth: FakeOAuth, settings: Settings
) -> None:
    sign_in(client, oauth)
    _expire(settings)

    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "session expired"


def test_an_unknown_cookie_is_not_a_session(client: TestClient) -> None:
    client.cookies.set(sessions.COOKIE_NAME, "not-a-token")

    assert client.get("/auth/me").status_code == 401


# -- what the stored row is and is not -------------------------------------------------


def test_the_table_holds_a_digest_and_not_the_token(
    client: TestClient, oauth: FakeOAuth, settings: Settings
) -> None:
    """Somebody reading `timothy.db` — which is also where the ban data is, so people do
    read it — must not come away holding live sessions."""
    sign_in(client, oauth)
    token = client.cookies[sessions.COOKIE_NAME]

    engine = create_engine(sync_url(settings.database_url))
    try:
        with engine.connect() as connection:
            stored = connection.execute(text("SELECT id FROM sessions")).scalars().all()
    finally:
        engine.dispose()

    assert stored == [sessions.digest(token)]
    assert token not in stored


def test_a_new_login_clears_out_the_expired_ones(
    client: TestClient, oauth: FakeOAuth, settings: Settings
) -> None:
    sign_in(client, oauth)
    _expire(settings)
    client.cookies.clear()
    sign_in(client, oauth)

    engine = create_engine(sync_url(settings.database_url))
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT COUNT(*) FROM sessions")).scalar()
    finally:
        engine.dispose()

    assert rows == 1


# -- the two credentials must not become each other ------------------------------------


def test_a_session_may_not_also_name_an_actor(client: TestClient, oauth: FakeOAuth) -> None:
    """The whole safety of the cookie is that the row names the actor. A client sending
    both is confused about which kind of caller it is, and is told so rather than having
    one of the two quietly win."""
    sign_in(client, oauth, user_id=MEMBER)

    response = client.get("/auth/me", headers={"X-Timothy-Actor": f"user:{POOL_ADMIN}"})

    assert response.status_code == 400
    assert "X-Timothy-Actor" in response.json()["detail"]


def test_a_wrong_token_is_refused_even_with_a_valid_session(
    client: TestClient, oauth: FakeOAuth
) -> None:
    """A bearer credential that is present must be correct. Falling back to the cookie
    would hide a service whose token has gone stale."""
    sign_in(client, oauth)

    response = client.get("/auth/me", headers=headers(token="not-the-token", actor=None))

    assert response.status_code == 401


def test_a_browser_cannot_be_talked_into_a_state_change_from_another_site(
    registered: TestClient, oauth: FakeOAuth
) -> None:
    """`SameSite=Lax` is the first lock and this is the second. The first is a browser
    default that an embedding or a stray `SameSite=None` could turn off silently."""
    sign_in(registered, oauth, user_id=POOL_ADMIN, guild_ids=(MANAGEMENT_GUILD,))

    response = registered.post(
        "/pools", json={"name": "spam"}, headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "cross-origin request refused"


def test_a_browser_state_change_with_no_origin_at_all_is_refused(
    registered: TestClient, oauth: FakeOAuth
) -> None:
    """Every browser sends `Origin` on a request that is not a GET, so a missing one is
    not a browser making an ordinary request."""
    sign_in(registered, oauth, user_id=POOL_ADMIN, guild_ids=(MANAGEMENT_GUILD,))

    assert registered.post("/pools", json={"name": "spam"}).status_code == 403


def test_a_same_origin_state_change_goes_through(
    registered: TestClient, oauth: FakeOAuth
) -> None:
    sign_in(registered, oauth, user_id=POOL_ADMIN, guild_ids=(MANAGEMENT_GUILD,))

    response = registered.post("/pools", json={"name": "spam"}, headers=ORIGIN)

    assert response.status_code == 201


def test_a_service_caller_is_not_asked_about_origins(registered: TestClient) -> None:
    """The check is for cookies. The bot holds a token, which no third-party page has."""
    response = registered.post("/pools", json={"name": "spam"}, headers=headers(POOL_ADMIN))

    assert response.status_code == 201


def test_a_call_with_neither_credential_is_refused(client: TestClient) -> None:
    response = client.get("/pools")

    assert response.status_code == 401
    assert response.json()["detail"] == "no credentials"


# -- what a session does and does not grant --------------------------------------------


def test_a_session_grants_nothing_by_itself(registered: TestClient, oauth: FakeOAuth) -> None:
    """Logging in says who you are. What you may do is still resolved against Discord —
    this user is in the management guild and holds nothing there."""
    sign_in(registered, oauth, user_id=MEMBER, guild_ids=(GUILD,))

    response = registered.post("/pools", json={"name": "spam"}, headers=ORIGIN)

    assert response.status_code == 403


def test_a_signed_in_pool_admin_is_told_they_manage_pools(
    registered: TestClient, oauth: FakeOAuth
) -> None:
    sign_in(registered, oauth, user_id=POOL_ADMIN, guild_ids=(MANAGEMENT_GUILD,))

    assert registered.get("/auth/me").json()["manages_pools"] is True


def test_a_signed_in_member_is_not(registered: TestClient, oauth: FakeOAuth) -> None:
    sign_in(registered, oauth, user_id=MEMBER, guild_ids=(GUILD,))

    assert registered.get("/auth/me").json()["manages_pools"] is False


def test_a_service_caller_has_an_identity_but_no_session(registered: TestClient) -> None:
    me = registered.get("/auth/me", headers=headers(POOL_ADMIN)).json()

    assert me["user_id"] == str(POOL_ADMIN)
    assert me["username"] is None
    assert me["expires_at"] is None
    assert me["manages_pools"] is True


def test_logging_out_without_a_session_is_not_an_error(registered: TestClient) -> None:
    response = registered.post("/auth/logout", headers=headers(POOL_ADMIN))

    assert response.status_code == 204


# -- the guild snapshot (ADR 0010) -----------------------------------------------------


def test_a_browser_is_scanned_only_against_the_guilds_discord_named(
    registered: TestClient, oauth: FakeOAuth, discord: FakeDiscord
) -> None:
    """Reading pools needs membership of some guild Timothy is in. For a browser that is
    answered from the intersection of two lists Discord provided, and confirmed against
    Discord for the guilds in it — never by scanning the rest."""
    sign_in(registered, oauth, user_id=MEMBER, guild_ids=(GUILD,))
    discord.calls.clear()

    assert registered.get("/pools").status_code == 200
    looked_up = discord.calls_of("fetch_member")
    assert [(call.guild_id, call.user_id) for call in looked_up] == [(GUILD, MEMBER)]


def test_a_browser_in_none_of_timothys_guilds_is_refused_without_asking_discord(
    registered: TestClient, oauth: FakeOAuth, discord: FakeDiscord
) -> None:
    """The carried-forward cost from phase 5: a genuine non-member used to pay a Discord
    call per guild — 52 seconds across the real deployment — before being told no."""
    sign_in(registered, oauth, user_id=OUTSIDER, guild_ids=(999_000_000_000_000_001,))
    discord.calls.clear()

    assert registered.get("/pools").status_code == 403
    assert discord.calls_of("fetch_member") == []


def test_the_snapshot_narrows_the_question_and_never_answers_it(
    registered: TestClient, oauth: FakeOAuth
) -> None:
    """Somebody who has left since logging in is refused, even though their snapshot
    still names the guild. The snapshot decides who to ask about, not what the answer
    is."""
    sign_in(registered, oauth, user_id=OUTSIDER, guild_ids=(GUILD,))

    assert registered.get("/pools").status_code == 403


def test_a_service_caller_still_scans_every_guild(
    registered: TestClient, discord: FakeDiscord
) -> None:
    """The bot brings no snapshot, so nothing about its path changed."""
    discord.calls.clear()

    assert registered.get("/pools", headers=headers(MEMBER)).status_code == 200
    scanned = {call.guild_id for call in discord.calls_of("fetch_member")}
    assert MANAGEMENT_GUILD in scanned


# -- configuration ---------------------------------------------------------------------


def test_an_unconfigured_internal_token_still_refuses_service_callers(
    settings: Settings, discord: FakeDiscord, oauth: FakeOAuth
) -> None:
    """A missing environment variable must not read as "no token needed", and adding a
    second way in must not have changed that."""
    open_settings = settings.model_copy(update={"internal_token": SecretStr("")})

    with TestClient(
        create_app(open_settings, discord_port=discord, oauth_port=oauth)
    ) as client:
        assert client.get("/pools", headers=headers()).status_code == 401


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://timothy.example.com", "https://timothy.example.com/api/auth/callback"),
        ("https://timothy.example.com/", "https://timothy.example.com/api/auth/callback"),
        ("", ""),
    ],
)
def test_the_redirect_uri_is_built_from_configuration(
    settings: Settings, base_url: str, expected: str
) -> None:
    """Discord matches this string exactly against what is registered, so it cannot be
    reconstructed from the request: behind the tunnel the backend sees an internal host."""
    assert redirect_uri(settings.model_copy(update={"public_base_url": base_url})) == expected
