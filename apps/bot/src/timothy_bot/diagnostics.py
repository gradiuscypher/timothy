"""What the gateway already knows about a guild, told to the backend.

The backend is the only Discord client (ADR 0003) and it stays that way: nothing here
makes a Discord call. Everything reported is read out of the cache the gateway filled for
free — role positions, Timothy's own resolved permissions, and above all how many people
hold each role, which Discord's REST API will not answer at any price short of paginating
every member of every guild (ADR 0016).

Reading it costs nothing because the bot already runs with the privileged `members`
intent, for "banned at the door" (ADR 0004), and discord.py chunks every guild at startup
as a result. This is that same cache, asked a different question.

Every function that talks to the backend swallows :class:`~timothy_bot.api.ApiError`, for
the reason :mod:`timothy_bot.relay` does: a backend that is down must not take the gateway
connection with it. A missed round is a stale snapshot, and the next round fixes it.
"""

import logging
from typing import Any

import discord

from timothy_bot.api import Api, ApiError

log = logging.getLogger(__name__)


def snapshot(guild: discord.Guild) -> dict[str, Any] | None:
    """Everything the backend needs about one guild, or `None` if it cannot be read.

    `None` for a guild Discord has not finished sending — during an outage a guild can
    arrive without Timothy's own member object, and reporting a `can_ban` of false for it
    would put a red banner in front of an administrator who has done nothing wrong.

    Member counts are reported only for a guild whose members were actually chunked.
    Otherwise every count is `None`, because `len(role.members)` over a half-filled cache
    is not a small error — it is a confident zero, which reads as "nobody is affected".
    """
    me = guild.me
    if me is None or guild.owner_id is None:
        log.debug("guild %s is not fully available yet; not reporting", guild.id)
        return None

    counted = guild.chunked
    return {
        # discord.py folds `ADMINISTRATOR` and ownership into resolved permissions, so
        # this is already the answer to "could Timothy ban anyone here at all".
        "can_ban": me.guild_permissions.ban_members,
        "is_administrator": me.guild_permissions.administrator,
        "top_role_position": me.top_role.position,
        "top_role_name": me.top_role.name,
        "owner_id": str(guild.owner_id),
        "member_counts_complete": counted,
        "roles": [
            {
                "role_id": str(role.id),
                "name": role.name,
                "position": role.position,
                "member_count": len(role.members) if counted else None,
                "managed": role.managed,
            }
            for role in guild.roles
            # `@everyone`'s ID is the guild's own. Reporting it would put a role every
            # single member holds at the bottom of the hierarchy list for no reason —
            # the same trap the backend's adapter documents when reading `member.roles`.
            if role.id != guild.id
        ],
    }


async def report(api: Api, guild: discord.Guild) -> bool:
    """Tell the backend what this guild looks like. `False` if it could not be told."""
    body = snapshot(guild)
    if body is None:
        return False
    try:
        await api.report_diagnostics(guild.id, body)
    except ApiError as error:
        log.warning("diagnostics for %s not reported: %s", guild.id, error.detail)
        return False
    return True


async def report_all(api: Api, guilds: list[discord.Guild]) -> int:
    """Report every guild, and say how many landed.

    The caller spaces these out; this does not, so that a one-off refresh of two guilds
    is not paced like a full round.
    """
    reported = 0
    for guild in guilds:
        if await report(api, guild):
            reported += 1
    return reported


async def requested(api: Api) -> frozenset[int]:
    """Which guilds an administrator has asked to have looked at again.

    Empty when the backend cannot be reached, which is the same as nobody having asked:
    the scheduled round covers every guild regardless, so a failure here delays a refresh
    rather than losing a guild.
    """
    try:
        guild_ids = await api.pending_diagnostics()
    except ApiError as error:
        log.warning("could not collect diagnostics requests: %s", error.detail)
        return frozenset()
    return frozenset(guild_ids)
