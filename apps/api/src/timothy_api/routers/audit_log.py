"""Reading back the append-only record.

Newest first, and paginated by id rather than offset: the table only ever grows at one
end, so a cursor stays correct while an offset silently shifts under a reader as new
rows arrive.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from timothy_api.deps import Requires, SessionDep
from timothy_api.policy import Operation
from timothy_api.schemas import AuditLogRead
from timothy_api.search import MAX_QUERY, matching
from timothy_core.actors import Actor
from timothy_core.db.models import AuditLogEntry

router = APIRouter(prefix="/audit-log", tags=["audit log"])

Reader = Annotated[Actor, Depends(Requires(Operation.READ_AUDIT_LOG))]

Limit = Annotated[int, Query(ge=1, le=200, description="How many entries to return.")]
Before = Annotated[
    int | None,
    Query(gt=0, description="Return entries older than this id. Omit for the newest page."),
]
Action = Annotated[
    str | None,
    Query(description="Only entries with this exact action, e.g. `listing.create`."),
]
Search = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=MAX_QUERY,
        description=(
            "Match against the actor, the action, the target, or the detail as text. "
            "A user ID finds every line about that user whichever of those they are in."
        ),
    ),
]


@router.get("")
async def read_audit_log(
    _actor: Reader,
    session: SessionDep,
    limit: Limit = 50,
    before_id: Before = None,
    action: Action = None,
    q: Search = None,
) -> list[AuditLogRead]:
    """The most recent entries, newest first.

    `action` and `q` narrow together rather than competing: the dropdown says which kind
    of line, the search box says which subject, and the pair of them is how somebody
    answers "when was this user banned" without reading a thousand rows.
    """
    query = select(AuditLogEntry).order_by(AuditLogEntry.id.desc()).limit(limit)
    if before_id is not None:
        query = query.where(AuditLogEntry.id < before_id)
    if action is not None:
        query = query.where(AuditLogEntry.action == action)
    if q is not None:
        query = query.where(
            matching(
                q,
                AuditLogEntry.actor,
                AuditLogEntry.action,
                AuditLogEntry.target,
                AuditLogEntry.detail,
            )
        )

    entries = await session.scalars(query)
    return [AuditLogRead.of(entry) for entry in entries]
