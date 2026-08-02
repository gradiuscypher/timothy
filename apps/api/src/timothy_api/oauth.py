"""The other door to Discord: the one a person walks through.

:class:`~timothy_core.ports.discord.DiscordPort` is Timothy acting with the bot token,
and ADR 0007 keeps it at five operations because those five can ban people. This is a
separate, smaller door with a different credential and no power at all: it proves that
the person at the browser is who they say they are, and it asks Discord which guilds they
are in. It cannot ban, unban, read permissions or post anything.

Keeping the two apart is the point. Nothing here is reachable from the enforcement
engine, and adding an operation here does not widen the surface ADR 0007 is guarding.

**The access token is used and thrown away.** Timothy stores no Discord tokens and has no
refresh story, because it never acts as the user — authority is resolved with the *bot*
token against Discord (ADR 0001), so a stored user token would be a credential with
nothing to do and somewhere to leak from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol, Self
from urllib.parse import urlencode

import httpx

if TYPE_CHECKING:
    from timothy_api.settings import Settings

API_BASE: Final = "https://discord.com/api/v10"
AUTHORIZE_URL: Final = "https://discord.com/oauth2/authorize"

SCOPES: Final = "identify guilds"
"""`identify` for who they are, `guilds` for where they are.

`guilds` is what lets a browser caller skip the scan of every guild Timothy is in — see
ADR 0010. It is the narrowest scope that answers the question; `guilds.members.read`
would answer more of it and is deliberately not asked for.
"""

CALLBACK_PATH: Final = "/api/auth/callback"
"""Where Discord sends the browser back to. Public path, not the backend's own: nginx
proxies `/api` to the backend with the prefix stripped, so the backend serves this as
`/auth/callback` while the browser and Discord only ever see the `/api` form."""


class OAuthError(Exception):
    """The login could not be completed.

    Every failure of the exchange lands here — a code that was already used, a client
    secret that is wrong, Discord being down. The caller turns it into one message,
    because a person who cannot log in cannot act on the difference.
    """


@dataclass(frozen=True, slots=True)
class DiscordIdentity:
    """Who logged in, and where Discord says they are.

    Attributes:
        user_id: the Discord user.
        username: what to call them.
        avatar: Discord's avatar hash, or `None` for the default avatar.
        guild_ids: every guild they are in, Timothy's or not. Intersected with Timothy's
            before it is used for anything (ADR 0010).
    """

    user_id: int
    username: str
    avatar: str | None
    guild_ids: tuple[int, ...]


class OAuthPort(Protocol):
    """Discord's authorization-code flow, as much of it as Timothy needs."""

    @property
    def configured(self) -> bool:
        """Whether a login can even be started.

        Unconfigured fails closed and says so: `/auth/login` answers 503 rather than
        sending somebody to Discord with an empty client ID and letting Discord explain.
        """
        ...

    def authorize_url(self, *, state: str) -> str:
        """Where to send the browser to ask Discord for consent."""
        ...

    async def identify(self, *, code: str) -> DiscordIdentity:
        """Redeem the code Discord handed back, and find out who it belongs to.

        Raises:
            OAuthError: the code was refused, or Discord could not be reached.
        """
        ...


def _snowflakes(payload: object) -> tuple[int, ...]:
    """Every `id` in a list of Discord objects, ignoring anything malformed."""
    if not isinstance(payload, list):
        return ()
    found = []
    for entry in payload:
        if isinstance(entry, dict):
            raw = entry.get("id")
            if isinstance(raw, str) and raw.isdigit():
                found.append(int(raw))
    return tuple(found)


class DiscordOAuth:
    """The real flow, over HTTPS to Discord."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        client: httpx.AsyncClient,
    ) -> None:
        """Hold the application's credentials and the client that will spend them."""
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = client

    @classmethod
    def create(cls, settings: Settings) -> Self:
        """Build the flow from process configuration."""
        return cls(
            client_id=settings.discord_client_id,
            client_secret=settings.discord_client_secret.get_secret_value(),
            redirect_uri=redirect_uri(settings),
            client=httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(10.0)),
        )

    async def close(self) -> None:
        """Release the connection pool."""
        await self._http.aclose()

    @property
    def configured(self) -> bool:
        """Whether all three of client ID, secret and public base URL are set."""
        return bool(self._client_id and self._client_secret and self._redirect_uri)

    def authorize_url(self, *, state: str) -> str:
        """Discord's consent screen, with the state to be handed back."""
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": SCOPES,
                "state": state,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    async def identify(self, *, code: str) -> DiscordIdentity:
        """Exchange, identify, list guilds, and forget the token.

        Raises:
            OAuthError: Discord refused the exchange or could not be reached.
        """
        access_token = await self._exchange(code)
        user = await self._get("/users/@me", access_token)
        guilds = await self._get("/users/@me/guilds", access_token)

        if not isinstance(user, dict):
            msg = "Discord's answer to /users/@me was not an object"
            raise OAuthError(msg)
        raw_id = user.get("id")
        if not isinstance(raw_id, str) or not raw_id.isdigit():
            msg = "Discord identified a user with no usable id"
            raise OAuthError(msg)

        avatar = user.get("avatar")
        return DiscordIdentity(
            user_id=int(raw_id),
            username=str(user.get("global_name") or user.get("username") or raw_id),
            avatar=avatar if isinstance(avatar, str) else None,
            guild_ids=_snowflakes(guilds),
        )

    async def _exchange(self, code: str) -> str:
        """Turn the authorization code into an access token."""
        payload = await self._post(
            "/oauth2/token",
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
        )
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            msg = "Discord returned no access token"
            raise OAuthError(msg)
        return token

    async def _post(self, path: str, form: dict[str, str]) -> Any:  # noqa: ANN401
        try:
            response = await self._http.post(path, data=form)
        except httpx.HTTPError as error:
            msg = f"could not reach Discord: {error}"
            raise OAuthError(msg) from error
        return self._body(response, path)

    async def _get(self, path: str, access_token: str) -> Any:  # noqa: ANN401
        try:
            response = await self._http.get(
                path, headers={"Authorization": f"Bearer {access_token}"}
            )
        except httpx.HTTPError as error:
            msg = f"could not reach Discord: {error}"
            raise OAuthError(msg) from error
        return self._body(response, path)

    @staticmethod
    def _body(response: httpx.Response, path: str) -> Any:  # noqa: ANN401
        """The parsed body, or an :class:`OAuthError` naming what failed.

        Deliberately does not repeat Discord's own error text: the exchange carries the
        client secret, and `invalid_client` responses have been known to echo the request
        back.
        """
        if not response.is_success:
            msg = f"Discord answered {response.status_code} to {path}"
            raise OAuthError(msg)
        try:
            return response.json()
        except ValueError as error:
            msg = f"Discord's answer to {path} was not JSON"
            raise OAuthError(msg) from error


def redirect_uri(settings: Settings) -> str:
    """Where Discord sends the browser back to, or `""` if that is not knowable.

    Must match a redirect URI registered on the Discord application exactly, character
    for character, which is why it is built from configuration rather than from the
    request.
    """
    if not settings.public_base_url:
        return ""
    return f"{settings.public_base_url.rstrip('/')}{CALLBACK_PATH}"
