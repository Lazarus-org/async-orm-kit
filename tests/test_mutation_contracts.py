"""Mutation input guards, plain SQLAlchemy models, and driver failure contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from test_model_lifecycle_contracts import (
    LifecycleRecord,
    SerializationDetail,
    lifecycle_sessions as lifecycle_sessions,
)

from async_orm_kit import QuerySet
from async_orm_kit.exceptions import DatabaseError, ModelAttributeError, QueryStateError
from async_orm_kit.observers.queryset_observer import BaseQuerySetObserver


@pytest.mark.asyncio
async def test_writer_validation_and_empty_mutations(lifecycle_sessions):
    async with lifecycle_sessions() as session:
        q = LifecycleRecord.objects.all(session)
        assert (
            await q.bulk_create([]) == 0
            and await q.bulk_create([], returning=True) == []
        )
        assert await q.update() == 0 and await q.update(returning=True) == []
        for kwargs, error in [
            ({"update_conflicts": True}, ValueError),
            (
                {
                    "ignore_conflicts": True,
                    "update_conflicts": True,
                    "unique_fields": ["id"],
                    "update_fields": ["name"],
                },
                ValueError,
            ),
            (
                {
                    "update_conflicts": True,
                    "unique_fields": ["nope"],
                    "update_fields": ["name"],
                },
                ModelAttributeError,
            ),
        ]:
            with pytest.raises(error):
                await q.bulk_create([{"name": "a"}], **kwargs)
        with pytest.raises(ModelAttributeError):
            await q.bulk_create([{"nope": "a"}])
        with pytest.raises(ModelAttributeError):
            await q.update(nope="a")
        with pytest.raises(QueryStateError):
            await q.select_for_update().delete()
        session.add(LifecycleRecord(name="a"))
        await session.flush()
        assert await q.add_observer(BaseQuerySetObserver()).delete() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["update", "delete"])
async def test_mutation_driver_failure(lifecycle_sessions, monkeypatch, method):
    async with lifecycle_sessions() as session:
        failure = SQLAlchemyError("write unavailable")
        monkeypatch.setattr(session, "execute", AsyncMock(side_effect=failure))
        with pytest.raises(DatabaseError) as error:
            await getattr(LifecycleRecord.objects.all(session), method)(
                **({"name": "a"} if method == "update" else {})
            )
        assert error.value.cause is failure


@pytest.mark.asyncio
async def test_create_with_plain_sqlalchemy_model(lifecycle_sessions, monkeypatch):
    async with lifecycle_sessions() as session:
        async with session.bind.begin() as conn:
            await conn.run_sync(SerializationDetail.__table__.create)
        row = await QuerySet(SerializationDetail, session).create(id=1)
        assert row.id == 1
        failure = SQLAlchemyError("insert unavailable")
        monkeypatch.setattr(session, "flush", AsyncMock(side_effect=failure))
        with pytest.raises(DatabaseError) as error:
            await QuerySet(SerializationDetail, session).create(id=2)
        assert error.value.cause is failure


@pytest.mark.asyncio
async def test_bulk_create_unsupported_dialect_and_mysql_flush_failure(
    lifecycle_sessions, monkeypatch
):
    async with lifecycle_sessions() as session:
        q = LifecycleRecord.objects.all(session)
        monkeypatch.setattr(
            session,
            "get_bind",
            lambda **kwargs: SimpleNamespace(dialect=SimpleNamespace(name="other")),
        )
        with pytest.raises(NotImplementedError):
            await q.bulk_create([{"name": "a"}], ignore_conflicts=True)
        monkeypatch.setattr(
            session,
            "get_bind",
            lambda **kwargs: SimpleNamespace(dialect=SimpleNamespace(name="mysql")),
        )
        failure = SQLAlchemyError("mysql insert failed")
        monkeypatch.setattr(session, "flush", AsyncMock(side_effect=failure))
        with pytest.raises(DatabaseError) as error:
            await q.bulk_create([{"name": "a"}], returning=True)
        assert error.value.cause is failure
