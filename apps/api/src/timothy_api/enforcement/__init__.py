"""Carrying out what :mod:`timothy_core.enforcement.decisions` decides.

The split is the whole design. `decide()` is a pure function over plain values and holds
the precedence argument; everything here gathers the state to ask it, and then does what
it answers — against Discord, through the port, behind ADR 0007's three rails.

The pieces, in the order a job passes through them:

* :mod:`~timothy_api.enforcement.state` — the database's half of the question, plus the
  one Discord call that answers the other half, made only when the answer depends on it.
* :mod:`~timothy_api.enforcement.engine` — the `Enforcer`: dry run, the circuit breaker,
  the ban or the notification, and the outcome recorded afterwards.
* :mod:`~timothy_api.enforcement.revert` — the other direction, which only ever touches
  bans Timothy has a recorded outcome for (ADR 0005).
* :mod:`~timothy_api.enforcement.handlers` — one function per `JobKind`, turning a thin
  payload into the set of (guild, user) questions it implies *now*.
* :mod:`~timothy_api.enforcement.worker` — claim, dispatch, retry with backoff.
* :mod:`~timothy_api.enforcement.sweep` — the safety net, staggered across guilds.
* :mod:`~timothy_api.enforcement.backfill` — the daily round of user-name lookups, which
  is not enforcement at all but rides the same queue, worker and pacer (ADR 0017).
"""

from timothy_api.enforcement.backfill import NameBackfiller
from timothy_api.enforcement.engine import Enforcer, Run
from timothy_api.enforcement.selfunbans import SelfUnbans
from timothy_api.enforcement.sweep import Sweeper
from timothy_api.enforcement.worker import JobContext, Worker

__all__ = [
    "Enforcer",
    "JobContext",
    "NameBackfiller",
    "Run",
    "SelfUnbans",
    "Sweeper",
    "Worker",
]
