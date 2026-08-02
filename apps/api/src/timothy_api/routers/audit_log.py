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


@router.get("")
async def read_audit_log(
    _actor: Reader,
    session: SessionDep,
    limit: Limit = 50,
    before_id: Before = None,
    action: Action = None,
) -> list[AuditLogRead]:
    """The most recent entries, newest first."""
    query = select(AuditLogEntry).order_by(AuditLogEntry.id.desc()).limit(limit)
    if before_id is not None:
        query = query.where(AuditLogEntry.id < before_id)
    if action is not None:
        query = query.where(AuditLogEntry.action == action)

    entries = await session.scalars(query)
    return [AuditLogRead.of(entry) for entry in entries]
