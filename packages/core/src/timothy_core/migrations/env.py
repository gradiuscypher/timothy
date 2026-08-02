"""Alembic environment.

Deliberately synchronous: `alembic` drives its own event loop otherwise, which blows up
when the caller already has one. `timothy_core.migrations.upgrade_to_head` strips the
`+aiosqlite` driver before handing the URL over.
"""

from typing import TYPE_CHECKING, Literal

from alembic import context
from sqlalchemy import engine_from_config, pool

from timothy_core.db.columns import ActorColumn, UtcDateTime
from timothy_core.db.models import Base

if TYPE_CHECKING:
    from alembic.autogenerate.api import AutogenContext

config = context.config
target_metadata = Base.metadata


def render_item(
    type_: str,
    obj: object,
    autogen_context: "AutogenContext",
) -> str | Literal[False]:
    """Render Timothy's column types as the plain SQL types they compile to.

    A migration is a historical record of DDL. It must not import application types,
    which are free to be renamed or deleted without rewriting history.
    """
    if type_ == "type" and isinstance(obj, UtcDateTime | ActorColumn):
        autogen_context.imports.add("import sqlalchemy as sa")
        return "sa.DateTime()" if isinstance(obj, UtcDateTime) else "sa.String(length=32)"
    return False


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a database, for review."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        render_item=render_item,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
            # SQLite cannot ALTER in place; every change is a table rebuild.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        # pysqlite only opens a transaction for DML, so the CREATE TABLEs land in
        # autocommit while the stamp of `alembic_version` does not. Without this the
        # schema exists and Alembic believes nothing has ever been applied.
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
