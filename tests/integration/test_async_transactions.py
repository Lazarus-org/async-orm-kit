from __future__ import annotations

import pytest
import pytest_asyncio

pytest.importorskip("aiosqlite")

from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from async_orm_kit import Model


class AsyncTransactionRecord(Model):
    __tablename__ = "phase2_async_transaction_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    code: Mapped[str] = mapped_column(String(80), unique=True)


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    database_path = tmp_path / "orm-phase2.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(AsyncTransactionRecord.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_model_save_does_not_commit_async_session(session_factory):
    async with session_factory() as session:
        record = AsyncTransactionRecord(name="rolled back", code="rollback")
        await record.save(session)
        assert record.id is not None
        assert session.in_transaction()
        await session.rollback()

    async with session_factory() as verifier:
        count = await verifier.scalar(
            select(func.count()).select_from(AsyncTransactionRecord)
        )
        assert count == 0


@pytest.mark.asyncio
async def test_manager_create_and_bulk_mutations_respect_transaction_scope(
    session_factory,
):
    async with session_factory() as session:
        async with session.begin():
            first = await AsyncTransactionRecord.objects.create(
                session, name="first", code="first"
            )
            await AsyncTransactionRecord.objects.bulk_create(
                session,
                [{"name": "second", "code": "second"}],
            )
            await AsyncTransactionRecord.objects.filter(session, code="first").update(
                name="updated"
            )
            assert first.name == "updated"
            count = await AsyncTransactionRecord.objects.filter(
                session, code="second"
            ).delete()
            assert count == 1

    async with session_factory() as verifier:
        records = (await verifier.scalars(select(AsyncTransactionRecord))).all()
        assert len(records) == 1
        assert records[0].name == "updated"


@pytest.mark.asyncio
async def test_update_fields_restores_unselected_dirty_values(session_factory):
    async with session_factory() as session:
        record = AsyncTransactionRecord(name="before", code="before")
        await record.save(session)
        await session.commit()

        record.name = "must-not-persist"
        record.code = "after"
        await record.save(session, update_fields=["code"])
        assert record.name == "before"
        assert record.code == "after"
        await session.commit()

    async with session_factory() as verifier:
        stored = await verifier.get(AsyncTransactionRecord, record.id)
        assert stored.name == "before"
        assert stored.code == "after"


@pytest.mark.asyncio
async def test_bulk_update_flushes_once_per_batch_and_does_not_commit(session_factory):
    async with session_factory() as session:
        first = AsyncTransactionRecord(name="one", code="one")
        second = AsyncTransactionRecord(name="two", code="two")
        session.add_all([first, second])
        await session.commit()

        first.name = "ignored-one"
        first.code = "updated-one"
        second.name = "ignored-two"
        second.code = "updated-two"
        count = await AsyncTransactionRecord.objects.bulk_update(
            session, [first, second], fields=["code"], batch_size=2
        )
        assert count == 2
        assert first.name == "one"
        assert second.name == "two"
        assert session.in_transaction()
        await session.rollback()

    async with session_factory() as verifier:
        records = (await verifier.scalars(select(AsyncTransactionRecord))).all()
        assert {record.code for record in records} == {"one", "two"}
        assert {record.name for record in records} == {"one", "two"}
