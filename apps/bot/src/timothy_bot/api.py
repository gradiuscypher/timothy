"""The backend, as the bot sees it.

Every slash command and every relayed gateway event is a call through here. The bot holds
no domain logic and no database (ADR 0003): it asserts *who* is acting and lets the
backend decide what that person may do.

Two headers go on every call, and a third when there is a guild to name. The bearer
token authenticates the container; the actor
header names whom the call is for. They are separate questions — see
`timothy_api.identity` — and the actor is always explicit, because a client that omitted
it would be claiming to be Timothy itself.

Paths are written here as templates and filled in with percent-encoded segments. Pool
names are typed by moderators and a pool called `spam/ham` must not address a different
route.
"""

import logging
from typing import Any, Final
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

SYSTEM: Final = "system"

FROM_GUILD_HEADER: Final = "X-Timothy-From-Guild"
"""Where the interaction came from. A hint for ordering, never a grant — see
:meth:`Api._headers`."""
"""The actor for Timothy's own business: registering guilds, relaying events."""


class ApiError(Exception):
    """The backend refused the call, or could not be reached.

    Carries the backend's own `detail` string, because that is what the moderator ends up
    reading in the red embed: "no such pool: spma" is a better answer than "400".
    """

    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        """Record what went wrong, and the status if there was one."""
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        """`None` when the request never got an answer at all."""


def _detail(response: httpx.Response) -> str:
    """The backend's explanation, or the bare status if it did not give one."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return str(body["detail"])
    return f"the backend answered {response.status_code}"


class Api:
    """Timothy's backend, bound to one actor.

    Instances are cheap and share the underlying connection pool: a command handler
    derives its own with :meth:`as_user`, which is the only way a human's identity ever
    reaches the API.
    """

    def __init__(
        self, client: httpx.AsyncClient, *, actor: str, from_guild: int | None = None
    ) -> None:
        """Wrap an HTTP client that already carries the internal token."""
        self._client = client
        self._actor = actor
        self._from_guild = from_guild

    def as_user(self, user_id: int, *, from_guild: int | None = None) -> "Api":
        """The same backend, acting for the moderator who typed the command.

        Never `system`: `Requirement.SYSTEM` is refused everything a person owns, and the
        reverse, so a command sent as Timothy would be rejected rather than escalated.

        `from_guild` is where the interaction came from. It is a *hint* and nothing more —
        the backend still resolves every permission against Discord (ADR 0001), and this
        only tells it which guild to look in first. See `X-Timothy-From-Guild` below.
        """
        return Api(self._client, actor=f"user:{user_id}", from_guild=from_guild)

    async def _request(
        self,
        method: str,
        template: str,
        /,
        body: dict[str, Any] | None = None,
        **segments: object,
    ) -> Any:  # noqa: ANN401 — the shape is the endpoint's, and each caller states it
        """Make one call, and turn any failure into an :class:`ApiError`."""
        path = template.format(
            **{name: quote(str(value), safe="") for name, value in segments.items()}
        )
        try:
            response = await self._client.request(
                method, path, json=body, headers=self._headers()
            )
        except httpx.HTTPError as error:
            message = f"could not reach Timothy's backend: {error}"
            raise ApiError(message) from error

        if response.is_success:
            return None if response.status_code == httpx.codes.NO_CONTENT else response.json()

        raise ApiError(_detail(response), status_code=response.status_code)

    def _headers(self) -> dict[str, str]:
        """Who this is for, and where they are standing.

        `X-Timothy-From-Guild` grants nothing. The one permission that has to scan every
        guild Timothy is in — "is this person a member of *any* of them", which is what
        reading pools requires — costs a Discord call per guild until it finds one, and
        Discord paces those at about two a second. Across a hundred-odd guilds that is
        most of a minute, and the bot gives up after 2.5 seconds because Discord closes
        the interaction at three.

        Saying which guild the command was typed in lets the backend look there first. It
        still asks Discord, and it still falls back to the full scan; all this changes is
        the order, which is the difference between one call and a hundred.
        """
        headers = {"X-Timothy-Actor": self._actor}
        if self._from_guild is not None:
            headers[FROM_GUILD_HEADER] = str(self._from_guild)
        return headers

    # -- guilds ------------------------------------------------------------------------

    async def register_guild(self, guild_id: int, *, name: str | None = None) -> dict[str, Any]:
        """Tell Timothy it is in a guild, and what it is called.

        Idempotent, and safe to repeat on reconnect — which is also what keeps the name
        current, since the gateway re-announces every guild each time it connects.
        """
        return await self._request(
            "PUT", "/guilds/{guild_id}", body={"name": name}, guild_id=guild_id
        )

    async def deregister_guild(self, guild_id: int) -> None:
        """Tell Timothy it has left a guild, and let its configuration cascade away."""
        await self._request("DELETE", "/guilds/{guild_id}", guild_id=guild_id)

    # -- diagnostics -------------------------------------------------------------------

    async def report_diagnostics(
        self, guild_id: int, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Tell the backend what the gateway sees of a guild (ADR 0016).

        Idempotent and wholesale: the backend replaces the guild's roles with what this
        carries, so one deleted in Discord stops being reported rather than lingering.
        """
        return await self._request(
            "PUT", "/guilds/{guild_id}/diagnostics", body=snapshot, guild_id=guild_id
        )

    async def pending_diagnostics(self) -> list[int]:
        """Guilds an administrator has asked to have looked at again, out of turn.

        The backend cannot reach the bot, so a refresh button is a row it records and this
        collects. Reading drains it: a request answers for one round, and a queue that
        needed acknowledging would grow without bound whenever the bot was down.
        """
        answered = await self._request("GET", "/diagnostics/pending")
        return [int(guild_id) for guild_id in answered["guild_ids"]]

    # -- pools and listings ------------------------------------------------------------

    async def create_pool(self, *, name: str, description: str) -> dict[str, Any]:
        """Create a pool."""
        return await self._request(
            "POST", "/pools", body={"name": name, "description": description}
        )

    async def delete_pool(self, name: str) -> None:
        """Delete a pool, its listings and every subscription to it."""
        await self._request("DELETE", "/pools/{name}", name=name)

    async def list_pools(self) -> list[dict[str, Any]]:
        """Every pool."""
        result: list[dict[str, Any]] = await self._request("GET", "/pools")
        return result

    async def create_listing(
        self, *, pool_name: str, user_id: int, reason: str
    ) -> dict[str, Any]:
        """List a user on a pool. An assertion, not an action — enforcement follows."""
        return await self._request(
            "POST",
            "/pools/{name}/listings",
            body={"user_id": str(user_id), "reason": reason},
            name=pool_name,
        )

    async def delete_listing(self, *, pool_name: str, user_id: int) -> None:
        """Remove a listing. The bans it caused stay: reverting is a separate ask."""
        await self._request(
            "DELETE", "/pools/{name}/listings/{user_id}", name=pool_name, user_id=user_id
        )

    async def list_user_listings(self, user_id: int) -> list[dict[str, Any]]:
        """Why this user is listed, across every pool."""
        result: list[dict[str, Any]] = await self._request(
            "GET", "/users/{user_id}/listings", user_id=user_id
        )
        return result

    # -- subscriptions -----------------------------------------------------------------

    async def list_subscriptions(self, guild_id: int) -> list[dict[str, Any]]:
        """What one guild has subscribed to."""
        result: list[dict[str, Any]] = await self._request(
            "GET", "/guilds/{guild_id}/subscriptions", guild_id=guild_id
        )
        return result

    async def set_subscription(
        self, *, guild_id: int, pool_name: str, level: str
    ) -> dict[str, Any]:
        """Subscribe to a pool, or change the level of an existing subscription."""
        return await self._request(
            "PUT",
            "/guilds/{guild_id}/subscriptions/{pool_name}",
            body={"level": level},
            guild_id=guild_id,
            pool_name=pool_name,
        )

    async def delete_subscription(self, *, guild_id: int, pool_name: str) -> None:
        """Unsubscribe. Bans already issued stay in place."""
        await self._request(
            "DELETE",
            "/guilds/{guild_id}/subscriptions/{pool_name}",
            guild_id=guild_id,
            pool_name=pool_name,
        )

    # -- exceptions --------------------------------------------------------------------

    async def list_exceptions(self, guild_id: int) -> list[dict[str, Any]]:
        """Everyone this guild has vouched for."""
        result: list[dict[str, Any]] = await self._request(
            "GET", "/guilds/{guild_id}/exceptions", guild_id=guild_id
        )
        return result

    async def create_exception(self, *, guild_id: int, user_id: int) -> dict[str, Any]:
        """Vouch for a user in this guild, from now on."""
        return await self._request(
            "PUT",
            "/guilds/{guild_id}/exceptions/{user_id}",
            body={"reason": None},
            guild_id=guild_id,
            user_id=user_id,
        )

    async def delete_exception(self, *, guild_id: int, user_id: int) -> None:
        """Withdraw the vouch, and let enforcement look at this user again."""
        await self._request(
            "DELETE",
            "/guilds/{guild_id}/exceptions/{user_id}",
            guild_id=guild_id,
            user_id=user_id,
        )

    # -- notification channel ----------------------------------------------------------

    async def read_notification_channel(self, guild_id: int) -> dict[str, Any]:
        """Where this guild's notifications go."""
        return await self._request(
            "GET", "/guilds/{guild_id}/notification-channel", guild_id=guild_id
        )

    async def set_notification_channel(
        self, *, guild_id: int, channel_id: int
    ) -> dict[str, Any]:
        """Point this guild's notifications at a channel."""
        return await self._request(
            "PUT",
            "/guilds/{guild_id}/notification-channel",
            body={"channel_id": str(channel_id)},
            guild_id=guild_id,
        )

    async def delete_notification_channel(self, guild_id: int) -> None:
        """Stop reporting to a channel."""
        await self._request(
            "DELETE", "/guilds/{guild_id}/notification-channel", guild_id=guild_id
        )

    # -- relayed gateway events --------------------------------------------------------

    async def member_joined(self, *, guild_id: int, user_id: int) -> str:
        """Relay `GUILD_MEMBER_ADD`, and return what the backend decided to do."""
        return await self._event("/events/member-join", guild_id=guild_id, user_id=user_id)

    async def ban_removed(self, *, guild_id: int, user_id: int) -> str:
        """Relay `GUILD_BAN_REMOVE`, and return what the backend decided to do."""
        return await self._event("/events/ban-remove", guild_id=guild_id, user_id=user_id)

    async def _event(self, path: str, *, guild_id: int, user_id: int) -> str:
        """Post an event and hand back the backend's one-line `action`.

        Snowflakes go as strings, as they do everywhere on this API: they are 64-bit, and
        the web UI's JSON parser is not.
        """
        acknowledged = await self._request(
            "POST", path, body={"guild_id": str(guild_id), "user_id": str(user_id)}
        )
        return str(acknowledged["action"])


def create_client(*, base_url: str, token: str, timeout: float) -> httpx.AsyncClient:
    """An HTTP client that holds the internal token and nothing else.

    The timeout is deliberately shorter than Discord's three-second interaction deadline.
    The API answers within it for everything a command does — mutations enqueue their
    fan-out rather than performing it — so a request that has not come back by then is a
    backend in trouble, and a moderator is better served by an error they can see than by
    an interaction that expires silently.
    """
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout),
        headers={"Authorization": f"Bearer {token}"},
    )
