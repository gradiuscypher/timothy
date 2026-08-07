"""`user_names.name` becomes nullable: "asked, and Discord had nobody".

Revision 0006 built the table when a name was the only thing worth recording, because
names arrived only from traffic and an ID nobody had seen simply had no row. The backfill
(ADR 0017) introduced a third state: an ID that *was* looked up and turned out to belong
to no account. Recording it is what stops every round re-asking about the same deleted
accounts for the life of the deployment, and NULL is how it is recorded.

Nothing is rewritten. Every existing row has a name and keeps it; the change is only that
new rows may omit one. A reader sees no difference either way — a NULL name and a missing
row both draw as the bare ID.

SQLite cannot alter a column in place, so this is a batch operation: Alembic rebuilds the
table and copies the rows, which is why revision 0001's naming convention exists.

Revision ID: 0007
Revises: 0006
Created: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Let a row say that Discord had nobody by that ID."""
    with op.batch_alter_table("user_names") as batch_op:
        batch_op.alter_column("name", existing_type=sa.String(length=64), nullable=True)


def downgrade() -> None:
    """Take the third state away again.

    The rows recording a failed lookup cannot survive a NOT NULL column and are deleted
    rather than given an invented name. They are bookkeeping, not data: what is lost is
    the knowledge that those IDs were already asked about, and the next backfill round
    asks again.
    """
    op.execute(sa.text("DELETE FROM user_names WHERE name IS NULL"))
    with op.batch_alter_table("user_names") as batch_op:
        batch_op.alter_column("name", existing_type=sa.String(length=64), nullable=False)
