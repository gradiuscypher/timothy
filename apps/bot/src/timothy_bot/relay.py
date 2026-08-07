"""What the gateway saw, told to the backend.

The bot relays and the backend decides (ADR 0003). Nothing here inspects a pool, a
subscription or an exception: it says a member joined, or a ban was lifted, and logs the
one-line `action` that comes back. That line is how an operator watching a manual unban
sees whether the automatic exception fired, was suppressed as Timothy's own revert, or
was skipped because the user is not listed in anything this guild enforces.

Every function here swallows :class:`~timothy_bot.api.ApiError`. A backend that is down
must not take the gateway connection with it — the sweep is the safety net for exactly
the events lost while this is failing, and a bot that crashed on the first 503 would lose
every subsequent event too.
"""

import logging

from timothy_bot.api import Api, ApiError

log = logging.getLogger(__name__)


async def member_joined(
    api: Api, *, guild_id: int, user_id: int, username: str | None = None
) -> None:
    """A user turned up in a guild. Enforcement is reactive, so this is the moment.

    The name goes with it for the same reason a guild's does — the gateway has it and the
    backend has no cheap way to ask — and for nothing else: no decision here or there
    reads it.
    """
    try:
        action = await api.member_joined(guild_id=guild_id, user_id=user_id, username=username)
    except ApiError as error:
        log.warning("member join %s in %s not relayed: %s", user_id, guild_id, error.detail)
        return
    log.info("member join %s in %s: %s", user_id, guild_id, action)


async def ban_removed(
    api: Api, *, guild_id: int, user_id: int, username: str | None = None
) -> None:
    """A ban was lifted in a guild. The backend decides whether it should stick."""
    try:
        action = await api.ban_removed(guild_id=guild_id, user_id=user_id, username=username)
    except ApiError as error:
        log.warning("unban %s in %s not relayed: %s", user_id, guild_id, error.detail)
        return
    log.info("unban %s in %s: %s", user_id, guild_id, action)


async def guild_joined(api: Api, guild_id: int, name: str | None = None) -> None:
    """Timothy is in a guild. Registering is idempotent and safe to repeat.

    The name goes with it because the gateway already has it — it arrives with the guild
    itself — and the backend has no cheap way to ask for it later. Sending it on every
    announcement is also what keeps it current after a rename.
    """
    try:
        await api.register_guild(guild_id, name=name)
    except ApiError as error:
        # Louder than the other relays: a failure here is not just a missed event to be
        # caught by the sweep, it silently degrades what the UI shows for that guild
        # (Snowflake instead of a name) until the next reconnect or reconciliation pass.
        log.exception("guild %s not registered: %s", guild_id, error.detail)
        return
    log.info("guild %s registered", guild_id)


async def guild_renamed(api: Api, guild_id: int, name: str) -> None:
    """A guild Timothy is in was renamed.

    The same idempotent registration call, given a name to store. Separate from
    :func:`guild_joined` only so the log says what happened; a rename that fails to
    relay is corrected by the next reconnect, which re-announces everything.
    """
    try:
        await api.register_guild(guild_id, name=name)
    except ApiError as error:
        log.exception("guild %s rename not relayed: %s", guild_id, error.detail)
        return
    log.info("guild %s renamed to %s", guild_id, name)


async def guild_left(api: Api, guild_id: int) -> None:
    """Timothy has been removed from a guild, and its configuration goes with it.

    Only ever called from `on_guild_remove`, which Discord raises for an actual removal —
    a guild that has merely gone unavailable in an outage raises a different event and
    must not reach here. Deregistering cascades a guild's subscriptions, exceptions and
    notification channel away, so mistaking an outage for a departure would delete
    configuration nobody asked to lose.
    """
    try:
        await api.deregister_guild(guild_id)
    except ApiError as error:
        log.warning("guild %s not deregistered: %s", guild_id, error.detail)
        return
    log.info("guild %s deregistered", guild_id)


async def announce_guilds(api: Api, guilds: list[tuple[int, str | None]]) -> None:
    """Re-register everything Timothy is in, after a connect or a reconnect.

    Additive only. Guilds Timothy was removed from while it was offline stay registered,
    because the gateway's guild list is not evidence of a departure — a partial `READY`
    during a Discord outage looks exactly like being kicked from everything at once, and
    acting on it would cascade away every guild's configuration. Those are cleaned up by
    hand with `DELETE /guilds/{id}`; until then the sweep records failed outcomes there
    and retries, which is the harmless direction to be wrong in.

    Each guild arrives with its name, so this is also the pass that fills in the names
    the backend never had: a deployment upgraded into stored names gets them on the bot's
    next connect, rather than from a migration that would have to talk to Discord.
    """
    for guild_id, name in guilds:
        await guild_joined(api, guild_id, name)
