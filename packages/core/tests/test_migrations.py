"""The migrations are the schema. These tests keep them and the models from drifting."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from timothy_core.db.models import Base
from timothy_core.migrations import downgrade_to_base, upgrade_to_head


def test_head_matches_the_models(sync_engine: Engine) -> None:
    """If this fails, someone changed a model without generating a migration."""
    with sync_engine.connect() as connection:
        difference = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert difference == []


def test_upgrade_records_the_revision(sync_engine: Engine) -> None:
    """pysqlite only opens a transaction for DML, so the stamp is easy to lose while the
    tables themselves land — leaving Alembic convinced nothing has ever been applied."""
    with sync_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == "0001"


def test_there_is_exactly_one_head(sync_engine: Engine) -> None:
    """Two heads means two people generated a revision from the same parent."""
    with sync_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == ("0001",)


def test_downgrade_leaves_nothing_behind(database_url: str, sync_engine: Engine) -> None:
    downgrade_to_base(database_url)

    assert inspect(sync_engine).get_table_names() == ["alembic_version"]


def test_the_migrations_can_be_replayed(database_url: str, sync_engine: Engine) -> None:
    """Down and back up again, because a downgrade that cannot be undone is a trap."""
    downgrade_to_base(database_url)
    upgrade_to_head(database_url)

    with sync_engine.connect() as connection:
        difference = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert difference == []
