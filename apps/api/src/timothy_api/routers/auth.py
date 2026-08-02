"""Logging in and out of the web UI.

These four routes are the only ones outside the internal token, because a browser
arriving for the first time has no credential to present — that is what it is here to
get. Nothing they expose is worth anything without completing Discord's consent screen:
`/login` is a redirect anybody could construct by hand, `/callback` needs a code Discord
only hands to the registered redirect URI, and `/me` and `/logout` answer 401 without a
session.

Authority is not granted here. A session says *who* someone is; what they may do is still
resolved against Discord on every request (ADR 0001), which is why logging in tells you
so little — the only permission it reports is whether the person owns pools, and that is
one cached Discord call made for the UI's benefit, not a grant.
"""

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from timothy_api import sessions
from timothy_api.deps import ResolverDep, SessionDep, SettingsDep
from timothy_api.identity import CallerDep
from timothy_api.oauth import OAuthError, OAuthPort
from timothy_api.schemas import SignedInRead

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

StateCookie = Annotated[
    str | None,
    Cookie(alias=sessions.STATE_COOKIE_NAME, description="The pending login's state."),
]


def _oauth(request: Request) -> OAuthPort:
    port: OAuthPort = request.app.state.oauth
    return port


def _require_configured(request: Request) -> OAuthPort:
    """The OAuth flow, or a 503 naming what is missing.

    Fails closed and loudly. A half-configured login that redirects to Discord with an
    empty client ID gets an error page from Discord, which is somebody else's error
    message about somebody else's problem.

    Raises:
        HTTPException: 503 if the application is not configured for login.
    """
    port = _oauth(request)
    if not port.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "login is not configured: set TIMOTHY_DISCORD_CLIENT_ID, "
                "TIMOTHY_DISCORD_CLIENT_SECRET and TIMOTHY_PUBLIC_BASE_URL"
            ),
        )
    return port


def _set_cookie(
    response: Response, name: str, value: str, *, max_age: int, secure: bool
) -> None:
    """One cookie, with the same rules every time.

    `SameSite=Lax` rather than `Strict`: the login *is* a cross-site navigation — Discord
    sends the browser back — and under `Strict` the state cookie would not come with it.
    Lax sends cookies on top-level navigations and withholds them from cross-site form
    posts and subresource requests, which is the distinction that matters here. Nothing
    on this API changes state on a GET, so a Lax cookie riding along on a navigation can
    do nothing.
    """
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


@router.get("/login", include_in_schema=False)
async def login(request: Request, settings: SettingsDep) -> RedirectResponse:
    """Send the browser to Discord's consent screen.

    The `state` is a random value in a short-lived cookie and in the URL, compared on the
    way back. That is what stops somebody feeding a victim's browser an authorization
    code of their own and logging them into an attacker's Discord account.

    Not in the schema: it is a place to navigate to, not a call the generated client
    should ever make. The UI links to it.
    """
    port = _require_configured(request)
    state = secrets.token_urlsafe(sessions.TOKEN_BYTES)

    response = RedirectResponse(
        port.authorize_url(state=state), status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    _set_cookie(
        response,
        sessions.STATE_COOKIE_NAME,
        state,
        max_age=int(sessions.STATE_LIFETIME.total_seconds()),
        secure=settings.session_cookie_secure,
    )
    return response


@router.get("/callback", include_in_schema=False)
async def callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    code: Annotated[str, Query(description="Discord's authorization code.")],
    state: Annotated[str, Query(description="What /login put in the state cookie.")],
    state_cookie: StateCookie = None,
) -> RedirectResponse:
    """Finish the login and put the browser back on the app.

    Redirects rather than returning JSON, in both directions: this URL is somewhere a
    browser lands, not somewhere it fetches, so a failure has to end up on a page that
    can say so. `/?login=failed` is what the SPA reads.

    Raises:
        HTTPException: 400 if the state does not match the one that was issued.
    """
    port = _require_configured(request)
    if not state_cookie or not secrets.compare_digest(state, state_cookie):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="login state did not match; start again from /api/auth/login",
        )

    try:
        identity = await port.identify(code=code)
    except OAuthError:
        log.exception("discord refused the login exchange")
        response = RedirectResponse("/?login=failed", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(sessions.STATE_COOKIE_NAME, path="/")
        return response

    token = await sessions.issue(session, identity, lifetime=settings.session_lifetime)
    log.info("signed in user %s", identity.user_id)

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(sessions.STATE_COOKIE_NAME, path="/")
    _set_cookie(
        response,
        sessions.COOKIE_NAME,
        token,
        max_age=int(settings.session_lifetime.total_seconds()),
        secure=settings.session_cookie_secure,
    )
    return response


@router.get("/me")
async def me(
    caller: CallerDep,
    settings: SettingsDep,
    session: SessionDep,
    resolver: ResolverDep,
) -> SignedInRead:
    """Who the caller is, and the one thing the UI needs to know they can do.

    `manages_pools` is a cached Discord lookup and `is_owner` is a set membership; both
    decide which navigation the SPA draws. They are conveniences, not gates: every route
    behind them resolves the same thing again for itself, so a stale `false` hides a link
    and a stale `true` produces a 403 rather than an escalation.

    Service callers get an answer too, with no session attached. That is what the bot's
    contract test reads, and what makes "am I talking to a backend that knows me" one
    call rather than a guess.
    """
    user_id = caller.actor.user_id
    manages_pools = user_id is not None and await resolver.is_administrator(
        guild_id=settings.management_guild_id, user_id=user_id
    )
    signed_in = None
    if caller.session_token is not None:
        signed_in = await sessions.lookup(session, caller.session_token)

    return SignedInRead(
        actor=str(caller.actor),
        user_id=user_id,
        username=signed_in.username if signed_in else None,
        avatar=signed_in.avatar if signed_in else None,
        expires_at=signed_in.expires_at if signed_in else None,
        manages_pools=manages_pools,
        is_owner=user_id is not None and user_id in settings.owner_ids,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(caller: CallerDep, session: SessionDep, response: Response) -> None:
    """End this session and clear the cookie.

    A service caller has no session to end, and gets the same 204: logging out is
    idempotent, and there is nothing here worth telling a caller apart over.
    """
    if caller.session_token is not None:
        await sessions.revoke(session, caller.session_token)
    response.delete_cookie(sessions.COOKIE_NAME, path="/")
