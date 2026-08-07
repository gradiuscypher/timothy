"""Putting names to the IDs a page is already showing, and IDs to a remembered name.

The first route resolves rather than looks up: the UI hands over every ID on the screen
at once and gets back the ones Timothy has a name for. That shape is the point. Names are
wanted on listings, exceptions, outcomes, ban failures and audit entries alike, and
threading a name through each of those response schemas would mean five joins for a label
that no caller may act on — while still missing the audit log, whose actors are strings
rather than columns.

The second runs that backwards, for the one screen where an ID is what the reader is
missing rather than what they have. It answers with candidates and nothing else: a name
is not a key (:mod:`timothy_api.usernames`), so a search can hand somebody an ID to look
up but can never itself be the thing a decision is made about.

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
from timothy_api.search import MAX_QUERY
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

NameQuery = Annotated[
    str,
    Query(
        alias="q",
        min_length=1,
        max_length=MAX_QUERY,
        description="Part of a name, matched anywhere in it and ignoring case.",
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


@router.get("/search")
async def search_names(q: NameQuery, _actor: Reader, session: SessionDep) -> list[UserNameRead]:
    """Users whose name contains `q`, at most `MAX_MATCHES` of them.

    A way of reaching the user lookup when the snowflake is the part nobody remembers.
    Finding nobody is an ordinary answer and comes back as an empty list, because a name
    Timothy has never seen is indistinguishable from one that belongs to nobody.

    Reading this needs what resolving a name needs. It discloses nothing a caller could
    not already have by asking about IDs one at a time; what it saves them is knowing
    which IDs to ask about.
    """
    return [
        UserNameRead(user_id=row.user_id, name=row.name, observed_at=row.observed_at)
        # Same guard as above, for the same reason: `search` returns only named rows, and
        # the type checker has no way to know it.
        for row in await usernames.search(session, q)
        if row.name is not None
    ]
