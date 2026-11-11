from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

POSTGRES_URL = os.getenv("ASYNC_ORM_KIT_POSTGRES_URL")
if not POSTGRES_URL:
    pytest.skip(
        "Set ASYNC_ORM_KIT_POSTGRES_URL to run PostgreSQL integration tests.",
        allow_module_level=True,
    )

pytest.importorskip("asyncpg")

from sqlalchemy import (  # noqa: E402
    DateTime,
    ForeignKey,
    Integer,
    String,
    event,
    func,
    select,
)  # noqa: E402
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Mapped, mapped_column, relationship  # noqa: E402

from async_orm_kit import Model  # noqa: E402
from async_orm_kit.exceptions import DatabaseError  # noqa: E402


class Phase5PostgresWorkspace(Model):
    __tablename__ = "phase5_postgres_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)

    documents: Mapped[list[Phase5PostgresDocument]] = relationship(
        back_populates="workspace",
        lazy="raise",
        passive_deletes=True,
    )


class Phase5PostgresDocument(Model):
    __tablename__ = "phase5_postgres_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        default=uuid4,
        unique=True,
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )
    labels: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)),
        default=list,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("phase5_postgres_workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )

    workspace: Mapped[Phase5PostgresWorkspace | None] = relationship(
        back_populates="documents",
        lazy="raise",
    )


POSTGRES_TABLES = [
    Phase5PostgresWorkspace.__table__,
    Phase5PostgresDocument.__table__,
]


@pytest_asyncio.fixture
async def postgres_environment():
    """Create isolated tables against the configured real PostgreSQL database."""
    assert POSTGRES_URL is not None
    url = make_url(POSTGRES_URL)
    if (
        url.drivername != "postgresql+asyncpg"
        or not url.database
        or not url.database.endswith("_test")
    ):
        raise RuntimeError(
            "ASYNC_ORM_KIT_POSTGRES_URL must use postgresql+asyncpg and a "
            "disposable *_test database."
        )

    engine = create_async_engine(POSTGRES_URL, pool_pre_ping=True)
    statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Model.metadata.drop_all(
                    sync_connection,
                    tables=POSTGRES_TABLES,
                    checkfirst=True,
                )
            )
            await connection.run_sync(
                lambda sync_connection: Model.metadata.create_all(
                    sync_connection,
                    tables=POSTGRES_TABLES,
                )
            )

        statements.clear()
        yield factory, statements
    finally:
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: Model.metadata.drop_all(
                        sync_connection,
                        tables=POSTGRES_TABLES,
                        checkfirst=True,
                    )
                )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record_statement)
            await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_async_session_crud_and_native_column_types(
    postgres_environment,
):
    factory, _statements = postgres_environment

    async with factory() as session:
        async with session.begin():
            document = await Phase5PostgresDocument.objects.create(
                session,
                slug="postgres-native-types",
                payload={"kind": "guide", "revision": 3, "published": True},
                labels=["python", "postgresql", "orm"],
            )

            assert document.id > 0
            assert isinstance(document.external_id, UUID)
            assert document.created_at.tzinfo is not None

        async with factory() as reader:
            loaded = await Phase5PostgresDocument.objects.get(
                reader,
                id=document.id,
            )
            assert loaded.external_id == document.external_id
            assert loaded.payload == {
                "kind": "guide",
                "revision": 3,
                "published": True,
            }
            assert loaded.labels == ["python", "postgresql", "orm"]

            by_uuid = await Phase5PostgresDocument.objects.filter(
                reader,
                external_id=document.external_id,
            ).first()
            assert by_uuid is not None
            assert by_uuid.id == document.id

            by_json = await Phase5PostgresDocument.objects.filter(
                reader,
                payload={"kind": "guide", "revision": 3, "published": True},
            ).first()
            assert by_json is not None
            assert by_json.id == document.id

            by_array = await Phase5PostgresDocument.objects.filter(
                reader,
                labels=["python", "postgresql", "orm"],
            ).first()
            assert by_array is not None
            assert by_array.id == document.id


@pytest.mark.asyncio
async def test_postgres_bulk_insert_returning_and_generated_values(
    postgres_environment,
):
    factory, _statements = postgres_environment

    async with factory() as session:
        async with session.begin():
            created = await Phase5PostgresDocument.objects.bulk_create(
                session,
                [
                    {
                        "slug": "postgres-bulk-one",
                        "payload": {"position": 1},
                        "labels": ["bulk", "one"],
                    },
                    {
                        "slug": "postgres-bulk-two",
                        "payload": {"position": 2},
                        "labels": ["bulk", "two"],
                    },
                ],
                returning=True,
            )

            assert len(created) == 2
            assert {item.slug for item in created} == {
                "postgres-bulk-one",
                "postgres-bulk-two",
            }
            assert all(item.id > 0 for item in created)
            assert all(isinstance(item.external_id, UUID) for item in created)
            assert all(item.created_at.tzinfo is not None for item in created)


@pytest.mark.asyncio
async def test_postgres_non_returning_bulk_insert_defaults_and_upsert_rowcounts(
    postgres_environment,
):
    factory, _statements = postgres_environment

    async with factory() as session:
        async with session.begin():
            inserted = await Phase5PostgresDocument.objects.bulk_create(
                session,
                [
                    {
                        "slug": "postgres-core-bulk-one",
                        "payload": {"revision": 1},
                        "labels": ["core", "bulk"],
                    },
                    {
                        "slug": "postgres-core-bulk-two",
                        "payload": {"revision": 2},
                        "labels": ["core", "bulk"],
                    },
                ],
            )
            assert inserted == 2

            updated = await Phase5PostgresDocument.objects.bulk_create(
                session,
                [
                    {
                        "slug": "postgres-core-bulk-one",
                        "payload": {"revision": 10},
                        "labels": ["core", "updated"],
                    }
                ],
                update_conflicts=True,
                unique_fields=["slug"],
                update_fields=["payload", "labels"],
            )
            assert updated == 1

        async with factory() as reader:
            records = await Phase5PostgresDocument.objects.filter(
                reader,
                slug__startswith="postgres-core-bulk-",
            ).order_by("slug")
            assert len(records) == 2
            assert all(isinstance(record.external_id, UUID) for record in records)
            assert all(record.created_at.tzinfo is not None for record in records)
            assert records[0].payload == {"revision": 10}
            assert records[0].labels == ["core", "updated"]


@pytest.mark.asyncio
async def test_postgres_on_conflict_do_update_upserts_and_returns_rows(
    postgres_environment,
):
    factory, _statements = postgres_environment

    async with factory() as session:
        async with session.begin():
            original = await Phase5PostgresDocument.objects.bulk_create(
                session,
                [
                    {
                        "slug": "postgres-upsert",
                        "payload": {"revision": 1},
                        "labels": ["initial"],
                    }
                ],
                returning=True,
            )
            original_id = original[0].id
            original_uuid = original[0].external_id

            updated = await Phase5PostgresDocument.objects.bulk_create(
                session,
                [
                    {
                        "slug": "postgres-upsert",
                        "payload": {"revision": 2},
                        "labels": ["updated", "upsert"],
                    }
                ],
                update_conflicts=True,
                unique_fields=["slug"],
                update_fields=["payload", "labels"],
                returning=True,
            )

            assert len(updated) == 1
            assert updated[0] is original[0]
            assert updated[0].id == original_id
            assert updated[0].external_id == original_uuid
            assert updated[0].payload == {"revision": 2}
            assert updated[0].labels == ["updated", "upsert"]


@pytest.mark.asyncio
async def test_postgres_on_conflict_do_nothing_reports_inserted_rows(
    postgres_environment,
):
    factory, _statements = postgres_environment

    async with factory() as session:
        async with session.begin():
            await Phase5PostgresDocument.objects.create(
                session,
                slug="postgres-ignore-existing",
                payload={"source": "existing"},
                labels=["existing"],
            )
            inserted = await Phase5PostgresDocument.objects.bulk_create(
                session,
                [
                    {
                        "slug": "postgres-ignore-existing",
                        "payload": {"source": "duplicate"},
                        "labels": ["duplicate"],
                    },
                    {
                        "slug": "postgres-ignore-new",
                        "payload": {"source": "new"},
                        "labels": ["new"],
                    },
                ],
                ignore_conflicts=True,
            )

            assert inserted == 1
            assert (
                await Phase5PostgresDocument.objects.filter(
                    session,
                    slug="postgres-ignore-existing",
                ).count()
                == 1
            )
            assert (
                await Phase5PostgresDocument.objects.filter(
                    session,
                    slug="postgres-ignore-new",
                ).count()
                == 1
            )


@pytest.mark.asyncio
async def test_postgres_update_returning_and_delete(postgres_environment):
    factory, _statements = postgres_environment

    async with factory() as session:
        async with session.begin():
            await Phase5PostgresDocument.objects.bulk_create(
                session,
                [
                    {
                        "slug": "postgres-update-one",
                        "payload": {"state": "pending"},
                        "labels": ["pending"],
                    },
                    {
                        "slug": "postgres-update-two",
                        "payload": {"state": "pending"},
                        "labels": ["pending"],
                    },
                ],
            )

            updated = await Phase5PostgresDocument.objects.filter(
                session,
                slug__startswith="postgres-update-",
            ).update(
                payload={"state": "complete"},
                labels=["complete", "postgres"],
                returning=True,
            )
            assert len(updated) == 2
            assert all(item.payload == {"state": "complete"} for item in updated)
            assert all(item.labels == ["complete", "postgres"] for item in updated)

            deleted = await Phase5PostgresDocument.objects.filter(
                session,
                slug="postgres-update-one",
            ).delete()
            assert deleted == 1

            remaining = await Phase5PostgresDocument.objects.filter(
                session,
                slug__startswith="postgres-update-",
            ).count()
            assert remaining == 1


@pytest.mark.asyncio
async def test_postgres_caller_owned_transaction_rolls_back_without_implicit_commit(
    postgres_environment,
):
    factory, _statements = postgres_environment

    async with factory() as writer:
        await writer.begin()
        await Phase5PostgresDocument.objects.create(
            writer,
            slug="postgres-transaction-rollback",
            payload={"temporary": True},
            labels=["rollback"],
        )

        async with factory() as reader:
            assert not await Phase5PostgresDocument.objects.filter(
                reader,
                slug="postgres-transaction-rollback",
            ).exists()

        await writer.rollback()

    async with factory() as verifier:
        assert not await Phase5PostgresDocument.objects.filter(
            verifier,
            slug="postgres-transaction-rollback",
        ).exists()


@pytest.mark.asyncio
async def test_postgres_read_committed_visibility_between_async_sessions(
    postgres_environment,
):
    factory, _statements = postgres_environment

    async with factory() as writer, factory() as reader:
        await writer.begin()
        await Phase5PostgresDocument.objects.create(
            writer,
            slug="postgres-committed-visibility",
            payload={"visible": True},
            labels=["transaction"],
        )

        assert not await Phase5PostgresDocument.objects.filter(
            reader,
            slug="postgres-committed-visibility",
        ).exists()

        await writer.commit()

        assert await Phase5PostgresDocument.objects.filter(
            reader,
            slug="postgres-committed-visibility",
        ).exists()


@pytest.mark.asyncio
async def test_postgres_select_related_and_prefetch_related_query_counts(
    postgres_environment,
):
    factory, statements = postgres_environment

    async with factory() as session:
        async with session.begin():
            workspace = await Phase5PostgresWorkspace.objects.create(
                session,
                name="postgres-loading-workspace",
            )
            await Phase5PostgresDocument.objects.bulk_create(
                session,
                [
                    {
                        "slug": "postgres-loading-one",
                        "payload": {"index": 1},
                        "labels": ["loading"],
                        "workspace_id": workspace.id,
                    },
                    {
                        "slug": "postgres-loading-two",
                        "payload": {"index": 2},
                        "labels": ["loading"],
                        "workspace_id": workspace.id,
                    },
                ],
            )

        statements.clear()
        documents = (
            await Phase5PostgresDocument.objects.select_related(
                session,
                "workspace",
            )
            .filter(slug__startswith="postgres-loading-")
            .order_by("slug")
        )

        select_statements = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        assert len(select_statements) == 1
        assert [document.workspace.name for document in documents] == [
            "postgres-loading-workspace",
            "postgres-loading-workspace",
        ]
        assert len(select_statements) == 1

        statements.clear()
        workspaces = await Phase5PostgresWorkspace.objects.prefetch_related(
            session,
            "documents",
        ).filter(name="postgres-loading-workspace")

        select_statements = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        assert len(select_statements) == 2
        assert [document.slug for document in workspaces[0].documents] == [
            "postgres-loading-one",
            "postgres-loading-two",
        ]
        assert len(select_statements) == 2


@pytest.mark.asyncio
async def test_postgres_explain_and_verbose_explain_use_async_connection(
    postgres_environment,
):
    factory, _statements = postgres_environment

    async with factory() as session:
        await Phase5PostgresDocument.objects.create(
            session,
            slug="postgres-explain",
            payload={"plan": "check"},
            labels=["explain"],
        )

        query = Phase5PostgresDocument.objects.filter(
            session,
            slug="postgres-explain",
        )
        plan = await query.explain()
        verbose_plan = await query.explain(verbose=True)
        analyze_plan = await query.explain(analyze=True)

        assert "postgres_documents" in plan
        assert "postgres_documents" in verbose_plan
        assert "Execution Time" in analyze_plan


@pytest.mark.asyncio
async def test_postgres_query_compilation_uses_postgresql_operators(
    postgres_environment,
):
    from sqlalchemy.dialects import postgresql

    factory, _statements = postgres_environment

    async with factory() as session:
        sql = Phase5PostgresDocument.objects.filter(
            session,
            slug__icontains="POSTGRES",
        ).sql(dialect=postgresql.dialect())

        assert "ILIKE" in sql
        assert "POSTGRES" not in sql


@pytest.mark.asyncio
async def test_postgres_foreign_key_set_null_and_unique_constraints(
    postgres_environment,
):
    factory, _statements = postgres_environment

    async with factory() as session:
        async with session.begin():
            workspace = await Phase5PostgresWorkspace.objects.create(
                session,
                name="postgres-delete-workspace",
            )
            document = await Phase5PostgresDocument.objects.create(
                session,
                slug="postgres-delete-workspace-document",
                payload={"workspace": "linked"},
                labels=["foreign-key"],
                workspace_id=workspace.id,
            )
            await session.delete(workspace)
            await session.flush()
            await session.refresh(document)
            assert document.workspace_id is None

    async with factory() as session:
        with pytest.raises(DatabaseError):
            await Phase5PostgresDocument.objects.create(
                session,
                slug="postgres-delete-workspace-document",
                payload={},
                labels=[],
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_postgres_advanced_jsonb_array_and_set_operations(postgres_environment):
    """Exercise Stage 3 expressions through actual asyncpg execution."""
    from async_orm_kit import (
        ArrayContains,
        ArrayIndex,
        ArrayLength,
        ArrayOverlap,
        F,
        JSONContains,
        JSONHasKey,
        JSONPath,
    )

    factory, _statements = postgres_environment
    async with factory() as session:
        async with session.begin():
            await Phase5PostgresDocument.objects.create(
                session,
                slug="advanced-json-1",
                payload={"user": {"name": "Ada", "age": 35}, "enabled": True},
                labels=["python", "postgresql"],
            )
            await Phase5PostgresDocument.objects.create(
                session,
                slug="advanced-json-2",
                payload={"user": {"name": "Grace", "age": 41}, "enabled": False},
                labels=["sql"],
            )

    async with factory() as reader:
        users = await (
            Phase5PostgresDocument.objects.annotate(
                reader,
                name=JSONPath(F("payload"), "user", "name", as_type="string"),
                first_label=ArrayIndex(F("labels"), 1),
                size=ArrayLength(F("labels")),
            )
            .values("slug", "name", "first_label", "size")
            .order_by("slug")
        )
        assert users == [
            {
                "slug": "advanced-json-1",
                "name": "Ada",
                "first_label": "python",
                "size": 2,
            },
            {
                "slug": "advanced-json-2",
                "name": "Grace",
                "first_label": "sql",
                "size": 1,
            },
        ]

        matching = await Phase5PostgresDocument.objects.filter(
            reader,
            JSONContains(F("payload"), {"enabled": True}),
            JSONHasKey(F("payload"), "user"),
            ArrayContains(F("labels"), ["python"]),
            ArrayOverlap(F("labels"), ["postgresql", "json"]),
        )
        assert [item.slug for item in matching] == ["advanced-json-1"]

        left = Phase5PostgresDocument.objects.filter(
            reader, slug__startswith="advanced-json"
        ).values_list("slug", flat=True)
        right = Phase5PostgresDocument.objects.filter(
            reader, slug="advanced-json-1"
        ).values_list("slug", flat=True)
        assert await left.intersection(right) == ["advanced-json-1"]
        assert await left.difference(right) == ["advanced-json-2"]
        assert await left.union(right).count() == 2

        cte = left.cte("selected_document_slugs")
        assert (
            await reader.execute(select(func.count()).select_from(cte))
        ).scalar_one() == 2
