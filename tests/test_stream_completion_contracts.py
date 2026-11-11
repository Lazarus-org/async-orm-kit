"""Streaming cancellation, plain async iterators, and compound JSON filters."""

from types import SimpleNamespace

import pytest
from sqlalchemy import DateTime
from test_advanced_sql import PgSQLItem, SQLItem, db as db, run
from test_file_lifecycle_contracts import LegacyStorage, wrapper
from test_model_lifecycle_contracts import (
    LifecycleRecord,
    lifecycle_sessions as lifecycle_sessions,
)

from async_orm_kit import QuerySet
from async_orm_kit.query.lookups import _lookup_date_part


class ChunkIterator:
    def __init__(self):
        self.remaining = [b"one", b"two"]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.remaining:
            raise StopAsyncIteration
        return self.remaining.pop(0)


@pytest.mark.asyncio
async def test_file_supports_plain_async_iterator_without_close():
    storage = LegacyStorage()
    storage.iter_chunks = lambda name, chunk_size: ChunkIterator()
    assert [chunk async for chunk in wrapper(storage).iter_chunks(3)] == [
        b"one",
        b"two",
    ]


@pytest.mark.asyncio
async def test_stream_closes_after_early_consumer_exit(lifecycle_sessions):
    async with lifecycle_sessions() as session:
        session.add_all([LifecycleRecord(name="one"), LifecycleRecord(name="two")])
        await session.flush()
        q = LifecycleRecord.objects.all(session)
        for stream in [q.stream(), q.union(q).stream()]:
            assert (await anext(stream)).name == "one"
            await stream.aclose()
        assert await q.count() == 2


def test_compound_json_paths_use_scalar_and_document_operators(db):
    q = QuerySet(SQLItem, db).values("id", "payload")
    combined = q.filter(id=1).union(q.filter(id=2))
    assert [row["id"] for row in run(combined.filter(payload__user__age__gte=40))] == [
        2
    ]
    pg = QuerySet(PgSQLItem, db).values("payload")
    binary = pg.union(pg).filter(payload__has_key="user")
    from sqlalchemy.dialects.postgresql import dialect

    assert "?" in binary.sql(dialect())


def test_date_part_handles_relationship_like_columns_without_type():
    for columns in [[], [SimpleNamespace(type=DateTime())]]:
        column = SimpleNamespace(property=SimpleNamespace(columns=columns))
        if not columns:
            with pytest.raises(TypeError):
                _lookup_date_part(column, "year", 2026)
        else:
            from sqlalchemy import column as sa_column

            column.__clause_element__ = lambda: sa_column("created_at", DateTime())
            assert "EXTRACT" in str(_lookup_date_part(column, "year", 2026))


@pytest.mark.asyncio
async def test_cursor_iteration_failure_always_closes_result(
    lifecycle_sessions, monkeypatch
):
    from unittest.mock import AsyncMock

    from async_orm_kit.exceptions import DatabaseError

    class BrokenResult:
        def __init__(self):
            self.close = AsyncMock()

        def unique(self):
            return self

        def scalars(self):
            return self

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("cursor disconnected")

    async with lifecycle_sessions() as session:
        q = LifecycleRecord.objects.all(session)
        combined = q.union(q)
        for query in [q, combined]:
            result = BrokenResult()
            monkeypatch.setattr(session, "stream", AsyncMock(return_value=result))
            with pytest.raises(DatabaseError) as error:
                await anext(query.stream())
            assert isinstance(error.value.cause, RuntimeError)
            result.close.assert_awaited_once()
