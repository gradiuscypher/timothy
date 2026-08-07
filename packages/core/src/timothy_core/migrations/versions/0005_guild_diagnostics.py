"""What Timothy can and cannot do in each guild, so an administrator can see it.

Two tables, both cascading from `guilds` and both empty on arrival. The rows come from
the bot, which reports every guild it is in within fifteen minutes of connecting
(ADR 0016), so a deployment upgraded into these tables fills them on the bot's next
start rather than from a migration that would have to talk to Discord — the same shape
as revision 0004's guild names.

`guild_roles.member_count` is nullable and that is load-bearing: a count of zero is a
claim that a role nobody can be banned out of holds nobody, and the bot cannot always
make that claim honestly.

Revision ID: 0005
Revises: 0004
Created: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _snowflake() -> sa.BigInteger:
    """A Discord ID: 64-bit everywhere, and what `INTEGER` already is on SQLite."""
    return sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    """Add the per-guild diagnostics snapshot and its role table."""
    op.create_table(
        "guild_diagnostics",
        sa.Column("guild_id", _snowflake(), nullable=False),
        sa.Column("can_ban", sa.Boolean(), nullable=False),
        sa.Column("is_administrator", sa.Boolean(), nullable=False),
        sa.Column("top_role_position", sa.Integer(), nullable=False),
        sa.Column("top_role_name", sa.String(length=100), nullable=True),
        sa.Column("owner_id", _snowflake(), nullable=False),
        sa.Column("member_counts_complete", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["guild_id"],
            ["guilds.guild_id"],
            name=op.f("fk_guild_diagnostics_guild_id_guilds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("guild_id", name=op.f("pk_guild_diagnostics")),
    )
    op.create_table(
        "guild_roles",
        sa.Column("guild_id", _snowflake(), nullable=False),
        sa.Column("role_id", _snowflake(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=True),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["guild_id"],
            ["guilds.guild_id"],
            name=op.f("fk_guild_roles_guild_id_guilds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("guild_id", "role_id", name=op.f("pk_guild_roles")),
    )


def downgrade() -> None:
    """Drop both.

    Nothing derives from them: they are a cache of Discord's own state, and the bot
    rebuilds them on its next round.
    """
    op.drop_table("guild_roles")
    op.drop_table("guild_diagnostics")
