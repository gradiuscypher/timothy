"""`jobs.status` gains `cancelled`.

An operator can now drop a queued job instead of waiting for it, and what that leaves
behind should not read as a failure: `/ops` counts failed jobs as a health signal, and a
round somebody deliberately dropped is not a thing going wrong.

The status column is a string with a CHECK constraint rather than a native enum — SQLite
has no enum type — so widening it means rebuilding the constraint. That is a table
rebuild under SQLite, which is what batch mode does and why revision 0001 gave every
constraint a stable name.

Revision ID: 0008
Revises: 0007
Created: 2026-08-07
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE: Final = ("pending", "running", "done", "failed")
AFTER: Final = (*BEFORE, "cancelled")

CONSTRAINT = "job_status"


def _status(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name=CONSTRAINT, native_enum=False, create_constraint=True)


def upgrade() -> None:
    """Let a job be cancelled."""
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column(
            "status", existing_type=_status(BEFORE), type_=_status(AFTER), nullable=False
        )


def downgrade() -> None:
    """Narrow it again.

    A cancelled job has to become something the old constraint accepts. `failed` is the
    honest choice of the four: it did not run and it never will, which is what `failed`
    already means to everything that reads this table.
    """
    op.execute(sa.text("UPDATE jobs SET status = 'failed' WHERE status = 'cancelled'"))
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column(
            "status", existing_type=_status(AFTER), type_=_status(BEFORE), nullable=False
        )
