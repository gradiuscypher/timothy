"""The last known name for a Discord user ID.

One table, empty on arrival and filled from ordinary traffic — a login, a relayed join, a
relayed unban — exactly as revision 0004's guild names are. There is nothing to fill in
here: a migration that wanted a name would have to talk to Discord.

`name` is NOT NULL as this revision leaves it, and revision 0007 relaxes that. Reading
the pair in order is the history: this table was built when a name was the only thing
worth recording, and recording "asked, and Discord had nobody" came with the backfill.

Revision ID: 0006
Revises: 0005
Created: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _snowflake() -> sa.BigInteger:
    """A Discord ID: 64-bit everywhere, and what `INTEGER` already is on SQLite."""
    return sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    """Add the user name cache."""
    op.create_table(
        "user_names",
        sa.Column("user_id", _snowflake(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_names")),
    )


def downgrade() -> None:
    """Drop it. Nothing derives from a name, so losing them costs only recognition."""
    op.drop_table("user_names")
