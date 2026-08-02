"""Column types shared by the models.

SQLite is untyped enough that the round trip is where correctness is lost: a datetime
goes in aware and comes back naive, an `Actor` goes in structured and comes back a
string. Both are fixed here, once, rather than at every call site.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.types import TypeDecorator

from timothy_core.actors import Actor

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect


class UtcDateTime(TypeDecorator[datetime]):
    """A timezone-aware UTC timestamp.

    SQLite stores no offset, so an aware datetime written through the plain `DateTime`
    type comes back naive and silently compares wrong. Everything is normalised to UTC
    on the way in and re-tagged on the way out; naive input is rejected rather than
    guessed at.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,  # noqa: ARG002 — fixed by the TypeDecorator interface
    ) -> datetime | None:
        """Normalise to naive UTC for storage.

        Raises:
            ValueError: if `value` is naive, since its intended offset is unknowable.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            msg = f"naive datetime {value!r}; Timothy stores UTC only"
            raise ValueError(msg)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,  # noqa: ARG002 — fixed by the TypeDecorator interface
    ) -> datetime | None:
        """Re-attach UTC on the way out."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class ActorColumn(TypeDecorator[Actor]):
    """An :class:`~timothy_core.actors.Actor`, stored as ``user:<snowflake>`` or ``system``."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(
        self,
        value: Actor | None,
        dialect: Dialect,  # noqa: ARG002 — fixed by the TypeDecorator interface
    ) -> str | None:
        """Render the actor."""
        return None if value is None else str(value)

    def process_result_value(
        self,
        value: str | None,
        dialect: Dialect,  # noqa: ARG002 — fixed by the TypeDecorator interface
    ) -> Actor | None:
        """Parse the actor back.

        Raises:
            ValueError: if the stored string is not a rendered actor.
        """
        return None if value is None else Actor.parse(value)
