"""What each guild is called, so the web UI can say it.

Nullable and unfilled: this revision adds the column and nothing else. The names arrive
from the gateway, which re-announces every guild Timothy is in on connect, so the first
time the bot reconnects after this deploys every row gets its name. Backfilling here
would mean a migration that talks to Discord.

Revision ID: 0004
Revises: 0003
Created: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the cached guild name."""
    with op.batch_alter_table("guilds", schema=None) as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Drop it again. Nothing derives from it, so nothing is lost but the display."""
    with op.batch_alter_table("guilds", schema=None) as batch_op:
        batch_op.drop_column("name")
