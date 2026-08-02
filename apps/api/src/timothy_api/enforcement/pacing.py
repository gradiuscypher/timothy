"""Waiting between rounds, in a way that can be interrupted cleanly.

The obvious shutdown — cancel the background tasks — is wrong here, and visibly so: a
task cancelled part-way through a transaction cannot finish closing its session, because
the cleanup is itself a coroutine and the next `await` raises `CancelledError` again. The
connection is then only released when the garbage collector gets to it, which is after
the engine has been disposed.

So the loops are not cancelled. They are *asked* to stop, and they check between units of
work, at a point where nothing is half-done. The pause between rounds waits on that same
request, so a sweeper on a one-hour interval still shuts down immediately.

Injecting a `Pacer` is also the test seam. A loop paced by something that says "stop"
after two rounds runs exactly two rounds, which is a far better thing to assert on than
a loop paced by a `sleep` that does nothing and therefore never ends.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress


class Pacer:
    """Spaces out a background loop, and carries the request to end it."""

    def __init__(self) -> None:
        """Start running."""
        self._stopping = asyncio.Event()

    @property
    def stopping(self) -> bool:
        """Whether the loop has been asked to finish."""
        return self._stopping.is_set()

    def stop(self) -> None:
        """Ask the loop to finish after its current unit of work."""
        self._stopping.set()

    async def pause(self, seconds: float) -> bool:
        """Wait up to `seconds`, or until asked to stop. `True` if it is time to stop."""
        if self._stopping.is_set():
            return True
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        return self._stopping.is_set()
