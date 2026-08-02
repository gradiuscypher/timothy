"""Stand-ins and helpers the bot's tests share.

A module rather than `conftest.py` so the test files can import it by name. Both test
directories in this workspace would otherwise be packages called `tests`, and only the
first of them resolves.

The bot is a client of two things it does not own, so both are stood in for. The backend
is an `httpx.MockTransport` that records what was asked of it and answers what the test
says it answers — the real API's shape is pinned separately, in `test_contract.py`, so
these can stay about what the bot sends and renders. Discord is not stood in for at all:
a command handler is an ordinary coroutine, and calling it is the test.
"""

import json
from typing import TYPE_CHECKING, Any, cast

import discord
import httpx
from discord import app_commands

from timothy_bot.api import Api

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

GUILD = 100_000_000_000_000_002
MODERATOR = 200_000_000_000_000_001
LISTED_USER = 300_000_000_000_000_001
CHANNEL = 400_000_000_000_000_001


class Backend:
    """A stand-in for the API: records every request, answers from a queue.

    An empty queue answers `200 {}`, which is what most of these tests want — they are
    about the call that went out and the embed that came back, not about the body.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._queued: list[httpx.Response] = []

    def replies(self, status_code: int = 200, body: object = None) -> None:
        """Queue one answer."""
        self._queued.append(httpx.Response(status_code, json=body))

    def fails(self, status_code: int, detail: str) -> None:
        """Queue one refusal, in the shape FastAPI refuses in."""
        self._queued.append(httpx.Response(status_code, json={"detail": detail}))

    def answers_with(self, response: httpx.Response) -> None:
        """Queue something the other two cannot express — an HTML error page, say."""
        self._queued.append(response)

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Record and answer."""
        self.requests.append(request)
        if self._queued:
            return self._queued.pop(0)
        return httpx.Response(200, json={})

    @property
    def request(self) -> httpx.Request:
        """The only request that was made. Fails loudly if that is not true."""
        assert len(self.requests) == 1, f"expected one request, got {len(self.requests)}"
        return self.requests[0]

    @property
    def sent(self) -> object:
        """The JSON body of the only request."""
        return json.loads(self.request.content)

    @property
    def path(self) -> str:
        """The path of the only request, still encoded.

        `url.path` decodes, which would hide the very thing the quoting is there for: a
        pool called `spam/ham` and a pool called `spam%2Fham` must not look alike.
        """
        return self.request.url.raw_path.decode()

    @property
    def called(self) -> tuple[str, str]:
        """The method and path of the only request, as a pair to assert on."""
        return self.request.method, self.path


class FakeResponse:
    """`interaction.response`, reduced to the one thing the handlers use."""

    def __init__(self) -> None:
        self.embed: discord.Embed | None = None

    def is_done(self) -> bool:
        return self.embed is not None

    async def send_message(self, *, embed: discord.Embed) -> None:
        self.embed = embed


class FakeFollowup:
    """`interaction.followup`, for the one path that reaches it: a handler that already
    answered and then raised."""

    def __init__(self) -> None:
        self.embed: discord.Embed | None = None

    async def send(self, *, embed: discord.Embed) -> None:
        self.embed = embed


class FakeClient:
    """`interaction.client`, reduced to the API it carries."""

    def __init__(self, api: Api) -> None:
        self.api = api


class FakeInteraction:
    """Everything `timothy_bot.commands.base` reaches for, and nothing else."""

    def __init__(
        self, api: Api, *, guild_id: int | None = GUILD, user_id: int = MODERATOR
    ) -> None:
        self.client = FakeClient(api)
        self.user = discord.Object(id=user_id)
        self.guild_id = guild_id
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.command: app_commands.Command[Any, ..., Any] | None = None


async def invoke(
    command: app_commands.Command[Any, ..., Any],
    interaction: FakeInteraction,
    /,
    **options: object,
) -> discord.Embed:
    """Run a command's handler and hand back the embed it answered with.

    The cast is the price of the stand-in: the handlers are typed against the real
    `discord.Interaction`, which cannot be constructed without a gateway payload, and
    they touch four attributes of it.
    """
    handler = cast("Callable[..., Awaitable[None]]", command.callback)
    await handler(cast("discord.Interaction", interaction), **options)
    assert interaction.response.embed is not None, "the command answered nothing"
    return interaction.response.embed


def field(embed: discord.Embed, name: str) -> str:
    """One named field's value."""
    for item in embed.fields:
        if item.name == name:
            return item.value or ""
    message = f"no field named {name}: {[item.name for item in embed.fields]}"
    raise AssertionError(message)


def is_green(embed: discord.Embed) -> bool:
    return embed.colour == discord.Colour.dark_green()


def is_red(embed: discord.Embed) -> bool:
    return embed.colour == discord.Colour.red()
