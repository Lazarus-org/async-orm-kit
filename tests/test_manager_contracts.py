"""Manager forwarding, batching, and persistence guards against SQLite."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from test_model_lifecycle_contracts import (
    LifecycleRecord,
    lifecycle_sessions as lifecycle_sessions,
)

from async_orm_kit import Manager, Sum
from async_orm_kit.exceptions import DatabaseError


@pytest.mark.asyncio
async def test_manager_queries_and_bound_proxy(lifecycle_sessions):
    with pytest.raises(ValueError, match="not bound"):
        Manager().get_queryset(object())
    manager = LifecycleRecord.objects.clone_for_model()
    manager.contribute_to_class(LifecycleRecord, "contract_objects")
    manager.add_query_hook(lambda qs: qs.filter(score__gte=2))
    async with lifecycle_sessions() as session:
        await LifecycleRecord.objects.bulk_create(
            session,
            [
                {"id": 1, "name": "a", "score": 1},
                {"id": 2, "name": "b", "score": 2},
                {"id": 3, "name": "c", "score": 3},
            ],
            batch_size=2,
        )
        assert await manager.count(session) == 2
        proxy = LifecycleRecord.objects.using(session)
        assert proxy._cache_ttl == 300 and await proxy.count() == 3
        assert (await proxy.first()).id == 1
        assert (
            await proxy.exists()
            and await proxy.exists(id=1)
            and not await proxy.exists(id=99)
        )
        assert [x.id for x in await proxy.offset(1).order_by("id")] == [2, 3]
        assert len(await proxy.distinct()) == 3
        assert (await proxy.only("id").first()).id == 1
        assert proxy.select_for_update().state.for_update is not None
        assert await proxy.aggregate(total=Sum("score")) == {"total": 6}
        assert "SELECT" in proxy.sql() and await proxy.explain()
        assert await proxy.in_bulk([]) == {}
        assert set(await proxy.in_bulk([1, 3])) == {1, 3}
        assert (await proxy.latest("-id")).id == 1
        row, created = await proxy.get_or_create(id=1)
        assert not created and row.id == 1
        row, created = await proxy.update_or_create(id=4, defaults={"name": "d"})
        assert created and row.id == 4
        row, created = await proxy.update_or_create(id=4)
        assert not created and row.name == "d"
        rows = await proxy.bulk_create(
            [{"name": "e"}, {"name": "f"}, {"name": "g"}], batch_size=2, returning=True
        )
        assert len(rows) == 3 and all(isinstance(row, LifecycleRecord) for row in rows)
    delattr(LifecycleRecord, "contract_objects")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [True, "2", 0, -1])
async def test_batch_size_guards(lifecycle_sessions, size):
    async with lifecycle_sessions() as session:
        with pytest.raises(ValueError, match="batch_size"):
            await LifecycleRecord.objects.bulk_create(session, [], batch_size=size)
        with pytest.raises(ValueError, match="batch_size"):
            await LifecycleRecord.objects.bulk_update(
                session, [LifecycleRecord(name="a")], ["name"], batch_size=size
            )


@pytest.mark.asyncio
async def test_bulk_update_state_guards(lifecycle_sessions):
    async with lifecycle_sessions() as session:
        manager = LifecycleRecord.objects
        assert await manager.bulk_update(session, [], []) == 0
        with pytest.raises(ValueError, match="fields"):
            await manager.bulk_update(session, [object()], [])
        with pytest.raises(TypeError, match="instances"):
            await manager.bulk_update(session, [object()], ["name"])
        row = LifecycleRecord(name="a")
        with pytest.raises(ValueError, match="persisted"):
            await manager.bulk_update(session, [row], ["name"])
        session.add(row)
        with pytest.raises(ValueError, match="persisted"):
            await manager.bulk_update(session, [row], ["name"])
        await session.flush()
        await session.delete(row)
        await session.flush()
        with pytest.raises(ValueError, match="persisted"):
            await manager.bulk_update(session, [row], ["name"])


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["refresh", "flush"])
async def test_bulk_update_driver_failures(lifecycle_sessions, monkeypatch, boundary):
    async with lifecycle_sessions() as session:
        row = LifecycleRecord(name="a")
        session.add(row)
        await session.flush()
        row.name = "b"
        row.score = 7
        failure = SQLAlchemyError("driver failed")
        monkeypatch.setattr(session, boundary, AsyncMock(side_effect=failure))
        with pytest.raises(DatabaseError) as error:
            await LifecycleRecord.objects.bulk_update(session, [row], ["name"])
        assert error.value.cause is failure
