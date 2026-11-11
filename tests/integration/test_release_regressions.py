"""Release audit regressions executed through native asynchronous SQLite."""

from __future__ import annotations

import asyncio
from contextlib import aclosing

import pytest
import pytest_asyncio
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, aliased, mapped_column

from async_orm_kit import Count, Exists, F, Model, OuterRef, QuerySet
from async_orm_kit.exceptions import QueryStateError


class ReleaseRecord(Model):
    __tablename__ = "release_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    score: Mapped[int] = mapped_column(default=0)


@pytest_asyncio.fixture
async def records():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(ReleaseRecord.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as session:
            session.add_all(
                [ReleaseRecord(id=i, name=f"item-{i}", score=i) for i in range(1, 5)]
            )
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_slice_composes_existing_offset_and_limit(records):
    async with records() as session:
        base = ReleaseRecord.objects.order_by(session, "id").offset(1).limit(2)
        assert [row.id for row in await base[1:9]] == [3]
        assert await base[2] == []
        assert base.state.offset_val == 1
        assert base.state.limit_val == 2


@pytest.mark.asyncio
async def test_integer_index_remains_lazy_one_row_queryset(records):
    async with records() as session:
        base = ReleaseRecord.objects.order_by(session, "id")
        indexed = base[1]
        assert isinstance(indexed, QuerySet)
        assert [row.id for row in await indexed] == [2]
        assert await base[99] == []


@pytest.mark.asyncio
async def test_first_respects_empty_limit(records):
    async with records() as session:
        assert await ReleaseRecord.objects.limit(session, 0).first() is None


@pytest.mark.asyncio
async def test_get_projection_annotation_alias_order(records):
    async with records() as session:
        query = (
            ReleaseRecord.objects.filter(session, id=1)
            .annotate(double=F("score") * 2, triple=F("score") * 3)
            .values("double", "name")
        )
        assert (
            await query.get()
            == (await query)[0]
            == {"double": 2, "name": "item-1", "triple": 3}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset,limit,expected", [(0, 0, []), (1, None, []), (0, 1, [1, 2, 3, 4])]
)
async def test_exists_preserves_inner_pagination(records, offset, limit, expected):
    async with records() as session:
        inner_model = aliased(ReleaseRecord)
        inner = QuerySet(inner_model, session).filter(id=OuterRef("id")).offset(offset)
        if limit is not None:
            inner = inner.limit(limit)
        found = await ReleaseRecord.objects.filter(session, Exists(inner)).order_by(
            "id"
        )
        assert [row.id for row in found] == expected


@pytest.mark.asyncio
async def test_alias_count_uses_alias_primary_key(records):
    async with records() as session:
        entity = aliased(ReleaseRecord)
        assert await QuerySet(entity, session).filter(id__gt=2).count() == 2


@pytest.mark.asyncio
async def test_refresh_reattaches_detached_instance(records):
    async with records() as session:
        row = await session.get(ReleaseRecord, 1)
        session.expunge(row)
    async with records() as session:
        assert await row.refresh_from_db(session) is row
        assert row.name == "item-1"


@pytest.mark.asyncio
async def test_manager_earliest_latest_ordering(records):
    async with records() as session:
        assert (await ReleaseRecord.objects.earliest(session)).id == 1
        assert (await ReleaseRecord.objects.latest(session)).id == 4
        assert (await ReleaseRecord.objects.earliest(session, "score")).id == 1
        assert (await ReleaseRecord.objects.latest(session, "score")).id == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("shape", ["limit", "offset", "grouped"])
async def test_writes_reject_ignored_selection_constraints(records, operation, shape):
    async with records() as session:
        query = ReleaseRecord.objects.all(session)
        if shape == "limit":
            query = query.limit(1)
        elif shape == "offset":
            query = query.offset(1)
        else:
            query = (
                query.values("score").annotate(total=Count("id")).filter(total__gt=1)
            )
        with pytest.raises(QueryStateError):
            await getattr(query, operation)(
                **({"score": 99} if operation == "update" else {})
            )
        assert (
            await session.scalars(select(ReleaseRecord.id).order_by(ReleaseRecord.id))
        ).all() == [1, 2, 3, 4]
        assert (
            await session.scalars(
                select(ReleaseRecord.score).order_by(ReleaseRecord.id)
            )
        ).all() == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_savepoint_failure_and_outer_rollback(records):
    async with records() as session:
        async with session.begin():
            await ReleaseRecord.objects.create(session, id=5, name="outer", score=5)
            with pytest.raises(RuntimeError, match="abort nested"):
                async with session.begin_nested():
                    await ReleaseRecord.objects.create(
                        session, id=6, name="nested", score=6
                    )
                    raise RuntimeError("abort nested")
            assert await ReleaseRecord.objects.filter(session, id=5).exists()
            assert not await ReleaseRecord.objects.filter(session, id=6).exists()
    async with records() as session:
        assert await ReleaseRecord.objects.filter(session, id=5).exists()


@pytest.mark.asyncio
async def test_stream_cancellation_closes_cursor_and_session_remains_usable(records):
    async with records() as session:
        ready = asyncio.Event()

        async def consume():
            async with aclosing(ReleaseRecord.objects.all(session).stream()) as stream:
                async for _ in stream:
                    ready.set()
                    await asyncio.Event().wait()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await ReleaseRecord.objects.count(session) == 4


@pytest.mark.asyncio
async def test_database_failure_does_not_log_bound_secrets(records, caplog):
    from async_orm_kit.exceptions import DatabaseError

    async with records() as session:
        with pytest.raises(DatabaseError) as error:
            await ReleaseRecord.objects.bulk_create(
                session,
                [{"id": 1, "name": "sensitive-value@example.invalid", "score": 42}],
            )
        assert error.value.cause is not None
        assert "sensitive-value@example.invalid" not in caplog.text
        await session.rollback()
