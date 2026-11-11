"""MySQL 8.4 / asyncmy integration contract.

All tests require an explicitly configured disposable database. A configured
service that is missing or broken causes a failure, not a silent skip.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

MYSQL_URL = os.getenv("ASYNC_ORM_KIT_MYSQL_URL")
if not MYSQL_URL:
    pytest.skip(
        "Set ASYNC_ORM_KIT_MYSQL_URL for live MySQL integration tests.",
        allow_module_level=True,
    )

pytest.importorskip("asyncmy")

from sqlalchemy import (  # noqa: E402
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Uuid,
    event,
    func,
    select,
)
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Mapped, mapped_column, relationship  # noqa: E402

from async_orm_kit import Model  # noqa: E402
from async_orm_kit.exceptions import DatabaseError  # noqa: E402
from async_orm_kit.observers.queryset_observer import BaseQuerySetObserver  # noqa: E402


class Stage3MySQLAccount(Model):
    __tablename__ = "phase5_mysql_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    records: Mapped[list[Stage3MySQLRecord]] = relationship(
        back_populates="account", lazy="raise"
    )


class Stage3MySQLRecord(Model):
    __tablename__ = "phase5_mysql_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), default=uuid4, unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    amount: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("phase5_mysql_accounts.id"), nullable=False
    )
    account: Mapped[Stage3MySQLAccount] = relationship(
        back_populates="records", lazy="raise"
    )


TABLES = (Stage3MySQLAccount.__table__, Stage3MySQLRecord.__table__)


@pytest_asyncio.fixture
async def mysql_environment():
    """Only touch dedicated tables in an explicitly disposable *_test database."""
    assert MYSQL_URL is not None
    url = make_url(MYSQL_URL)
    if (
        url.drivername != "mysql+asyncmy"
        or not url.database
        or not url.database.endswith("_test")
    ):
        raise RuntimeError(
            "ASYNC_ORM_KIT_MYSQL_URL must be a mysql+asyncmy URL to a disposable *_test database."
        )

    engine = create_async_engine(MYSQL_URL, pool_pre_ping=True)
    statements: list[str] = []

    def count_statements(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count_statements)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: Model.metadata.drop_all(
                    sync, tables=TABLES, checkfirst=True
                )
            )
            await conn.run_sync(
                lambda sync: Model.metadata.create_all(sync, tables=TABLES)
            )
        statements.clear()
        yield factory, statements
    finally:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(
                    lambda sync: Model.metadata.drop_all(
                        sync, tables=TABLES, checkfirst=True
                    )
                )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count_statements)
            await engine.dispose()


async def seed_account(factory, name="Ada"):
    async with factory() as session:
        async with session.begin():
            account = await Stage3MySQLAccount.objects.create(session, name=name)
            return account.id


def record(slug: str, account_id: int, **overrides):
    from decimal import Decimal

    return {
        "slug": slug,
        "account_id": account_id,
        "payload": {"slug": slug, "active": True},
        "amount": Decimal("12.50"),
        **overrides,
    }


@pytest.mark.asyncio
async def test_mysql_native_types_defaults_and_persistent_crud(mysql_environment):
    factory, _ = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as session:
        async with session.begin():
            obj = await Stage3MySQLRecord.objects.create(
                session, **record("first", account_id)
            )
            assert obj.id > 0
            assert isinstance(obj.token, UUID)
            assert obj.quantity == 7
            assert obj.created_at is not None

    async with factory() as session:
        fetched = await Stage3MySQLRecord.objects.get(session, slug="first")
        assert fetched.id == obj.id
        assert fetched.payload["active"] is True
        assert str(fetched.amount) == "12.50"
        assert fetched.token == obj.token
        assert await Stage3MySQLRecord.objects.filter(session, slug="first").exists()
        assert await Stage3MySQLRecord.objects.count(session) == 1


@pytest.mark.asyncio
async def test_mysql_core_bulk_default_values_and_affected_row_count(mysql_environment):
    factory, _ = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as session:
        async with session.begin():
            result = await Stage3MySQLRecord.objects.bulk_create(
                session,
                [record("a", account_id), record("b", account_id)],
            )
            assert result == 2
    async with factory() as session:
        rows = await Stage3MySQLRecord.objects.all(session).order_by("slug")
        assert [row.slug for row in rows] == ["a", "b"]
        assert len({row.token for row in rows}) == 2
        assert all(row.quantity == 7 for row in rows)


@pytest.mark.asyncio
async def test_mysql_bulk_returning_uses_orm_flush_without_returning_sql(
    mysql_environment,
):
    factory, statements = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as session:
        statements.clear()
        async with session.begin():
            created = await Stage3MySQLRecord.objects.bulk_create(
                session,
                [record("one", account_id), record("two", account_id)],
                returning=True,
            )
            assert len(created) == 2
            assert all(isinstance(obj.id, int) for obj in created)
            assert len({obj.token for obj in created}) == 2
            assert all(obj.quantity == 7 for obj in created)
            assert all(obj.created_at is not None for obj in created)
            assert not any(" RETURNING " in sql.upper() for sql in statements)


@pytest.mark.asyncio
async def test_mysql_on_duplicate_key_update_and_affected_row_count(mysql_environment):
    factory, _ = mysql_environment
    account_id = await seed_account(factory)
    await seed_account(factory, "Grace")
    async with factory() as session:
        async with session.begin():
            await Stage3MySQLRecord.objects.create(session, **record("dup", account_id))

    async with factory() as session:
        async with session.begin():
            rowcount = await Stage3MySQLRecord.objects.bulk_create(
                session,
                [record("dup", account_id, payload={"version": 2})],
                update_conflicts=True,
                update_fields=["payload"],
                unique_fields=["slug"],
            )
            # MySQL reports affected rows, not inserted+updated object counts.
            assert isinstance(rowcount, int)
            assert rowcount >= 1

    async with factory() as session:
        obj = await Stage3MySQLRecord.objects.get(session, slug="dup")
        assert obj.payload == {"version": 2}
        assert await Stage3MySQLRecord.objects.count(session) == 1


@pytest.mark.asyncio
async def test_mysql_insert_ignore_skips_conflicting_unique_rows(mysql_environment):
    factory, _ = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as session:
        async with session.begin():
            await Stage3MySQLRecord.objects.create(
                session, **record("existing", account_id)
            )
    async with factory() as session:
        async with session.begin():
            count = await Stage3MySQLRecord.objects.bulk_create(
                session,
                [record("existing", account_id), record("new", account_id)],
                ignore_conflicts=True,
            )
            assert count == 1
    async with factory() as session:
        assert await Stage3MySQLRecord.objects.count(session) == 2


@pytest.mark.asyncio
async def test_mysql_returning_conflict_and_update_are_rejected_before_write(
    mysql_environment,
):
    factory, _ = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as session:
        async with session.begin():
            await Stage3MySQLRecord.objects.create(
                session, **record("existing", account_id)
            )
    async with factory() as session:
        async with session.begin():
            with pytest.raises(NotImplementedError, match="MySQL 8"):
                await Stage3MySQLRecord.objects.bulk_create(
                    session,
                    [record("existing", account_id, payload={"changed": True})],
                    returning=True,
                    update_conflicts=True,
                    update_fields=["payload"],
                    unique_fields=["slug"],
                )
            with pytest.raises(NotImplementedError, match="MySQL 8"):
                await Stage3MySQLRecord.objects.filter(session, slug="existing").update(
                    returning=True, quantity=123
                )
    async with factory() as session:
        obj = await Stage3MySQLRecord.objects.get(session, slug="existing")
        assert obj.quantity == 7
        assert obj.payload["slug"] == "existing"


@pytest.mark.asyncio
async def test_mysql_filtered_update_delete_and_rollback(mysql_environment):
    factory, _ = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as session:
        async with session.begin():
            await Stage3MySQLRecord.objects.create(
                session, **record("target", account_id)
            )
            await Stage3MySQLRecord.objects.create(
                session, **record("stay", account_id)
            )

    async with factory() as session:
        async with session.begin():
            assert (
                await Stage3MySQLRecord.objects.filter(session, slug="target").update(
                    quantity=3
                )
                == 1
            )
            assert (
                await Stage3MySQLRecord.objects.filter(session, slug="stay").delete()
                == 1
            )

    async with factory() as session:
        await session.begin()
        obj = await Stage3MySQLRecord.objects.get(session, slug="target")
        assert obj.quantity == 3
        await Stage3MySQLRecord.objects.create(
            session, **record("rollback", account_id)
        )
        await session.rollback()

    async with factory() as session:
        assert not await Stage3MySQLRecord.objects.filter(
            session, slug="rollback"
        ).exists()
        assert await Stage3MySQLRecord.objects.count(session) == 1


class _RecordingDeleteObserver(BaseQuerySetObserver):
    def __init__(self):
        self.deleted: list[Any] = []

    async def post_bulk_delete(self, **kwargs):
        self.deleted = list(kwargs["doc_ids"])


@pytest.mark.asyncio
async def test_mysql_delete_observer_preselects_locked_keys(mysql_environment):
    factory, _ = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as session:
        async with session.begin():
            item = await Stage3MySQLRecord.objects.create(
                session, **record("deleted", account_id)
            )
    observer = _RecordingDeleteObserver()
    async with factory() as session:
        async with session.begin():
            removed = (
                await Stage3MySQLRecord.objects.filter(session, slug="deleted")
                .add_observer(observer)
                .delete()
            )
            assert removed == 1
            assert observer.deleted == [item.id]


@pytest.mark.asyncio
async def test_mysql_transaction_isolation_between_sessions(mysql_environment):
    factory, _ = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as writer:
        async with writer.begin():
            await Stage3MySQLRecord.objects.create(
                writer, **record("uncommitted", account_id)
            )
            async with factory() as reader:
                assert not await Stage3MySQLRecord.objects.filter(
                    reader, slug="uncommitted"
                ).exists()
    async with factory() as reader:
        assert await Stage3MySQLRecord.objects.filter(
            reader, slug="uncommitted"
        ).exists()


@pytest.mark.asyncio
async def test_mysql_relationship_loading_and_query_count(mysql_environment):
    factory, statements = mysql_environment
    first_id = await seed_account(factory)
    second_id = await seed_account(factory, "Grace")
    async with factory() as session:
        async with session.begin():
            await Stage3MySQLRecord.objects.create(session, **record("one", first_id))
            await Stage3MySQLRecord.objects.create(session, **record("two", first_id))
            await Stage3MySQLRecord.objects.create(
                session, **record("three", second_id)
            )

    async with factory() as session:
        statements.clear()
        entries = await Stage3MySQLRecord.objects.select_related(
            session, "account"
        ).order_by("id")
        assert [entry.account.name for entry in entries] == ["Ada", "Ada", "Grace"]
        assert (
            len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1
        )

    async with factory() as session:
        statements.clear()
        accounts = await Stage3MySQLAccount.objects.prefetch_related(
            session, "records"
        ).order_by("id")
        assert [[r.slug for r in a.records] for a in accounts] == [
            ["one", "two"],
            ["three"],
        ]
        assert (
            len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 2
        )


@pytest.mark.asyncio
async def test_mysql_explain_uses_async_connection_without_interpolating_values(
    mysql_environment,
):
    factory, statements = mysql_environment
    await seed_account(factory)
    async with factory() as session:
        statements.clear()
        plan = await Stage3MySQLAccount.objects.filter(session, name="Ada").explain()
        assert plan
        assert "Ada" not in "\n".join(statements)
        assert any(s.lstrip().upper().startswith("EXPLAIN") for s in statements)


@pytest.mark.asyncio
async def test_mysql_unique_constraint_failure_preserves_original_error(
    mysql_environment,
):
    factory, _ = mysql_environment
    await seed_account(factory)
    async with factory() as session:
        with pytest.raises(DatabaseError) as exc:
            await Stage3MySQLAccount.objects.create(session, name="Ada")
        assert exc.value.__cause__ is not None
        await session.rollback()


@pytest.mark.asyncio
async def test_mysql_advanced_json_paths_and_set_operations(mysql_environment):
    """Exercise Stage 3 portable SQL against real MySQL 8.4/asyncmy."""
    from async_orm_kit import F, JSONPath

    factory, _statements = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as session:
        async with session.begin():
            await Stage3MySQLRecord.objects.create(
                session,
                **record(
                    "advanced-json-a",
                    account_id,
                    payload={"user": {"name": "Ada", "age": 35}},
                ),
            )
            await Stage3MySQLRecord.objects.create(
                session,
                **record(
                    "advanced-json-b",
                    account_id,
                    payload={"user": {"name": "Grace", "age": 41}},
                ),
            )

    async with factory() as reader:
        results = await (
            Stage3MySQLRecord.objects.annotate(
                reader,
                username=JSONPath(F("payload"), "user", "name", as_type="string"),
                age=JSONPath(F("payload"), "user", "age", as_type="integer"),
            )
            .filter(age__gte=40)
            .values("slug", "username")
            .order_by("slug")
        )
        assert len(results) == 1
        assert results[0]["slug"] == "advanced-json-b"
        assert results[0]["username"] == "Grace"

        left = Stage3MySQLRecord.objects.filter(
            reader, slug__startswith="advanced-json"
        ).values_list("slug", flat=True)
        right = Stage3MySQLRecord.objects.filter(
            reader, slug="advanced-json-a"
        ).values_list("slug", flat=True)
        assert sorted(await left.union(right)) == ["advanced-json-a", "advanced-json-b"]
        assert await left.intersection(right) == ["advanced-json-a"]
        assert await left.difference(right) == ["advanced-json-b"]
        cte = left.cte("selected_record_slugs")
        assert (
            await reader.execute(select(func.count()).select_from(cte))
        ).scalar_one() == 2


@pytest.mark.asyncio
async def test_mysql_delete_observer_without_filter_locks_all_keys(mysql_environment):
    factory, _ = mysql_environment
    account_id = await seed_account(factory)
    async with factory() as session:
        async with session.begin():
            item = await Stage3MySQLRecord.objects.create(
                session, **record("delete-all", account_id)
            )
    observer = _RecordingDeleteObserver()
    async with factory() as session:
        async with session.begin():
            removed = (
                await Stage3MySQLRecord.objects.all(session)
                .add_observer(observer)
                .delete()
            )
            assert removed == 1
            assert observer.deleted == [item.id]
