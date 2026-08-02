"""Browser sessions: issuing them, looking them up, and letting them go.

A session is the browser's half of ADR 0008. The bot presents the internal token and
names an actor; a browser presents a cookie that *is* both — the row names the actor, so
there is nothing for a caller to assert and nothing to forge.

Two properties are load-bearing:

* **The stored id is a digest, not the token.** The browser holds
  `secrets.token_urlsafe(32)`; the table holds its SHA-256. Reading the table gives you
  no session, which matters because the same file holds the ban data an operator is far
  more likely to be poking at with `sqlite3`.
* **Expiry is checked in the lookup, not by a sweeper.** An expired row that is still
  present is not a valid session, so there is no window in which forgetting to prune is
  a security problem. Pruning is housekeeping, and happens when a new session is issued.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import delete, select

from timothy_core.actors import Actor
from timothy_core.db.models import Session

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from timothy_api.oauth import DiscordIdentity

COOKIE_NAME: Final = "timothy_session"
STATE_COOKIE_NAME: Final = "timothy_oauth_state"

STATE_LIFETIME: Final = timedelta(minutes=10)
"""How long somebody has to get through Discord's consent screen. Long enough to read
it, short enough that a state cookie left on a shared machine is not a standing invitation
to complete a login somebody else started."""

TOKEN_BYTES: Final = 32


def new_token() -> str:
    """A fresh session token for a browser to hold."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def digest(token: str) -> str:
    """The stored form of a token: 64 hex characters, which is the column's width."""
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SignedIn:
    """A live session, as the rest of the application sees it.

    Attributes:
        actor: whom this browser is acting as. Never `system` — Timothy does not log in.
        guild_ids: Discord's answer at login to "which guilds is this user in". A
            snapshot, used only to narrow what gets asked about (ADR 0010).
        username: for the UI's corner.
        avatar: Discord's avatar hash, or `None`.
        expires_at: when the browser will have to log in again.
    """

    actor: Actor
    guild_ids: frozenset[int]
    username: str
    avatar: str | None
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


async def issue(
    session: AsyncSession,
    identity: DiscordIdentity,
    *,
    lifetime: timedelta,
    now: Callable[[], datetime] = _now,
) -> str:
    """Record a new session and hand back the token for the browser.

    Prunes anything already expired on the way through: the table has an index on
    `expires_at` for exactly this, and a login is the natural moment to pay for it.
    """
    moment = now()
    await session.execute(delete(Session).where(Session.expires_at <= moment))

    token = new_token()
    session.add(
        Session(
            id=digest(token),
            user_id=identity.user_id,
            username=identity.username,
            avatar=identity.avatar,
            guild_ids=list(identity.guild_ids),
            created_at=moment,
            expires_at=moment + lifetime,
        )
    )
    await session.commit()
    return token


async def lookup(
    session: AsyncSession, token: str, *, now: Callable[[], datetime] = _now
) -> SignedIn | None:
    """The session this token names, or `None` if there isn't a live one.

    An expired row answers `None` and is left where it is. Deleting it would mean a read
    path that writes, and on SQLite with one writer that is a lock taken on every request
    to save a row that the next login will clear anyway.
    """
    row = await session.scalar(select(Session).where(Session.id == digest(token)))
    if row is None or row.expires_at <= now():
        return None
    return SignedIn(
        actor=Actor.user(row.user_id),
        guild_ids=frozenset(row.guild_ids),
        username=row.username,
        avatar=row.avatar,
        expires_at=row.expires_at,
    )


async def revoke(session: AsyncSession, token: str) -> None:
    """Forget a session. Logging out twice is not an error."""
    await session.execute(delete(Session).where(Session.id == digest(token)))
    await session.commit()
