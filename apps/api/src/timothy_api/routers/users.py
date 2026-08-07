"""Putting names to the IDs a page is already showing.

One route, and it resolves rather than looks up: the UI hands over every ID on the screen
at once and gets back the ones Timothy has a name for. That shape is the point. Names are
wanted on listings, exceptions, outcomes, ban failures and audit entries alike, and
threading a name through each of those response schemas would mean five joins for a label
that no caller may act on — while still missing the audit log, whose actors are strings
rather than columns.

Reading a name needs what reading a listing needs. A name is not a further disclosure:
anybody entitled to see that an ID is listed is entitled to see what Discord publicly
calls that ID.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from timothy_api import usernames
from timothy_api.deps import Requires, SessionDep
from timothy_api.policy import Operation
from timothy_api.schemas import Snowflake, UserNameRead
from timothy_core.actors import Actor

router = APIRouter(prefix="/users", tags=["users"])

Reader = Annotated[Actor, Depends(Requires(Operation.READ_POOLS))]

Ids = Annotated[
    list[Snowflake],
    Query(
        alias="id",
        default_factory=list,
        max_length=usernames.MAX_LOOKUP,
        description="A user ID to resolve. Repeat it for each ID on the page.",
    ),
]


@router.get("/names")
async def resolve_names(ids: Ids, _actor: Reader, session: SessionDep) -> list[UserNameRead]:
    """The last known name for each of these IDs, for the ones there is one for.

    An ID with no name is simply absent from the answer, and the caller shows the ID. A
    missing name is never an error and never a 404: it is the ordinary state of a user
    Timothy has not happened to see.
    """
    return [
        UserNameRead(user_id=row.user_id, name=row.name, observed_at=row.observed_at)
        # `resolve` returns only named rows; the guard is what says so to the type
        # checker, and it is the reason a row recording "Discord had nobody" — which is a
        # NULL name — cannot leak out of here as an empty string.
        for row in await usernames.resolve(session, ids)
        if row.name is not None
    ]
