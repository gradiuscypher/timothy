"""Why a job's last attempt failed.

The worker retries with backoff and eventually gives up. Without this column, giving up
is silent: the row says `failed` and nothing says why, so the only way to find out is to
reproduce the failure with debug logging on.

Revision ID: 0002
Revises: 0001
Created: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add `jobs.last_error`, nullable — a job that has not failed has nothing to say."""
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop it again."""
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("last_error")
