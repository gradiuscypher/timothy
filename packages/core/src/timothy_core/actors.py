"""Who did a thing.

Every mutation records an actor. Most actors are Discord users, but some actions are
Timothy's own — auto-subscribing a guild on join (ADR 0002), auto-creating an exception
after an unban (ADR 0006). The old bot attributed those to the magic user ID ``"0"``,
which was indistinguishable from a real user and made the exception list unreadable.

An actor is therefore a tagged value, stored as ``user:<snowflake>`` or ``system``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Self

SYSTEM: Final = "system"
USER_PREFIX: Final = "user:"


@dataclass(frozen=True, slots=True)
class Actor:
    """A Discord user, or Timothy itself.

    Construct through :meth:`user` or :meth:`system` rather than directly; the
    ``user_id`` of ``None`` meaning "Timothy" is an implementation detail.
    """

    user_id: int | None

    @classmethod
    def user(cls, user_id: int) -> Self:
        """The Discord user who asked for this."""
        return cls(user_id=user_id)

    @classmethod
    def system(cls) -> Self:
        """Timothy acting on its own initiative."""
        return cls(user_id=None)

    @property
    def is_system(self) -> bool:
        """Whether this is Timothy rather than a person."""
        return self.user_id is None

    def __str__(self) -> str:
        """Render for storage and for logs."""
        return SYSTEM if self.user_id is None else f"{USER_PREFIX}{self.user_id}"

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Read back what :meth:`__str__` wrote.

        Raises:
            ValueError: if `raw` is neither ``system`` nor ``user:<digits>``.
        """
        if raw == SYSTEM:
            return cls.system()
        if raw.startswith(USER_PREFIX):
            digits = raw.removeprefix(USER_PREFIX)
            if digits.isdigit():
                return cls.user(int(digits))
        msg = f"not an actor: {raw!r}"
        raise ValueError(msg)
