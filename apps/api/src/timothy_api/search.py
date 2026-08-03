"""Free-text search, spelled the same way on every screen that has a box for it.

Three tables are read by typing into them — the listings on a pool, the audit log and the
job queue — and all three want the same thing: a case-insensitive substring, across a
handful of columns whose contents are only sometimes text.

Every column is compared *as text*, which is the point rather than a shortcut. A partial
snowflake has to match, because somebody reading a screenshot types the six digits they
can make out and not all eighteen; and an audit detail or a job payload is JSON, which
has no other useful comparison at this size. What that costs is a scan, and what it buys
is one box that finds a user ID wherever it happens to be recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import String, cast, or_

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement, SQLColumnExpression

LIKE_ESCAPE = "\\"

MAX_QUERY = 128
"""Long enough for a reason or a snowflake, short enough that nobody is scanning the
table with a novel."""


def _pattern(query: str) -> str:
    """`query` as a LIKE pattern that matches it anywhere, wildcards defanged.

    `%` and `_` are escaped because somebody searching for `100%` is searching for a
    string and not writing a pattern.
    """
    pattern = query
    for character in (LIKE_ESCAPE, "%", "_"):
        pattern = pattern.replace(character, LIKE_ESCAPE + character)
    return f"%{pattern}%"


def matching(query: str, *columns: SQLColumnExpression[Any]) -> ColumnElement[bool]:
    """Any of these columns containing `query`, compared as text and ignoring case.

    A `NULL` column simply does not match — `NULL OR TRUE` is still true — so nullable
    columns can be listed without guarding them.

    `SQLColumnExpression` and not `ColumnElement`, because a mapped attribute — which is
    what every caller passes — is not one of those. It is the type the two have in
    common.
    """
    pattern = _pattern(query)
    return or_(*(cast(column, String).ilike(pattern, escape=LIKE_ESCAPE) for column in columns))
