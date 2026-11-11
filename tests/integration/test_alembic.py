from __future__ import annotations

import sys

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Column, Index, Integer, MetaData, String, Table, create_engine
from sqlalchemy.orm import Mapped, mapped_column

from async_orm_kit import Base, Model
from async_orm_kit.integrations.alembic import get_target_metadata


class AlembicIntegrationRecord(Model):
    __tablename__ = "phase5_alembic_integration_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)


def test_get_target_metadata_imports_model_modules_and_returns_shared_metadata():
    metadata = get_target_metadata(sys.modules[__name__])

    assert metadata is Base.metadata
    assert "phase5_alembic_integration_records" in metadata.tables
    assert AlembicIntegrationRecord.__table__ in metadata.sorted_tables


def test_get_target_metadata_accepts_module_names():
    assert get_target_metadata("async_orm_kit.models.meta") is Base.metadata


def test_get_target_metadata_rejects_invalid_module_arguments():
    with pytest.raises(ValueError, match="must not be empty"):
        get_target_metadata("  ")

    with pytest.raises(TypeError, match="module names or module objects"):
        get_target_metadata(object())  # type: ignore[arg-type]


def test_alembic_autogenerate_detects_and_clears_schema_drift():
    metadata = MetaData()
    Table(
        "alembic_autogenerate_items",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(80), nullable=False),
        Index("ix_alembic_autogenerate_items_name", "name"),
    )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            initial_diffs = compare_metadata(context, metadata)
            assert any(
                diff[0] == "add_table" and diff[1].name == "alembic_autogenerate_items"
                for diff in initial_diffs
            )

            metadata.create_all(connection)
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            remaining_diffs = compare_metadata(context, metadata)
            assert not remaining_diffs
    finally:
        engine.dispose()


def test_alembic_migration_context_uses_model_metadata_without_private_registry():
    metadata = get_target_metadata()
    assert isinstance(metadata, MetaData)
    assert metadata is Base.metadata
    assert (
        metadata.tables[AlembicIntegrationRecord.__tablename__]
        is AlembicIntegrationRecord.__table__
    )
