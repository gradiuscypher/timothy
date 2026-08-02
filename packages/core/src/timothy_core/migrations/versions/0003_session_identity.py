"""What a browser session knows about the person holding it.

The `sessions` table landed with the schema in phase 1 and nothing wrote to it until
phase 6. Three columns are added rather than the table being rewritten, because the
schema is already deployed and a released revision is not edited in place.

`username` and `avatar` are there so rendering "signed in as ..." costs no Discord call.
`guild_ids` is the OAuth `guilds` snapshot ADR 0010 describes: the set of guilds Discord
said this user was in at login, used to narrow the membership scan rather than to grant
anything.

Revision ID: 0003
Revises: 0002
Created: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the identity a session carries.

    Server defaults, not just Python-side ones: the table is empty everywhere today, but
    a `NOT NULL` column added to a table that might not be would fail on the row it
    could not fill, and there is no reason to find that out in production.
    """
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("username", sa.String(length=64), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("avatar", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("guild_ids", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    """Drop them again."""
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_column("guild_ids")
        batch_op.drop_column("avatar")
        batch_op.drop_column("username")
