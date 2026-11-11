"""Set operation shapes, validation, cache, and database failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from test_model_lifecycle_contracts import (
    LifecycleRecord,
    SerializationOwner,
    lifecycle_sessions as lifecycle_sessions,
)

from async_orm_kit import F, Q, QuerySet
from async_orm_kit.exceptions import DatabaseError, QueryStateError
from async_orm_kit.queryset.compound import CompoundQuerySet, _python_result_type


@pytest.mark.asyncio
async def test_compound_native_shapes_and_cache(lifecycle_sessions):
    async with lifecycle_sessions() as session:
        session.add_all(
            [LifecycleRecord(id=1, name="a"), LifecycleRecord(id=2, name="b")]
        )
        await session.flush()
        for shape in ["model", "dict", "tuple", "flat"]:
            base = LifecycleRecord.objects.all(session)
            if shape == "dict":
                base = base.values("id")
            elif shape == "tuple":
                base = base.values_list("id", "name")
            elif shape == "flat":
                base = base.values_list("id", flat=True)
            compound = base.filter(id=1).union(base.filter(id=2))
            rows = await compound
            assert len(rows) == 2 and await compound is rows
            assert len([row async for row in compound]) == 2
            assert compound.subquery("combined").name == "combined"
            assert "UNION" in compound.sql()
            assert len(await compound.filter()) == 2
        assert [
            row.id for row in await compound_models(session).filter(Q(id=1) | ~Q(id=2))
        ] == [1]


def compound_models(session):
    base = LifecycleRecord.objects.all(session)
    return base.filter(id=1).union(base.filter(id=2))


def test_compound_constructor_and_filter_validation(monkeypatch):
    session = object()
    base = QuerySet(LifecycleRecord, session)
    with pytest.raises(ValueError):
        CompoundQuerySet([base, base], "bogus")
    for branches in [[base], [base, object()]]:
        with pytest.raises(TypeError):
            CompoundQuerySet(branches, "union")
    with pytest.raises(ValueError, match="same mapped"):
        base.union(QuerySet(SerializationOwner, session))
    for invalid in [
        base.only("name"),
        base.annotate(copy=F("id")),
        base.distinct("id"),
    ]:
        with pytest.raises(QueryStateError):
            invalid.union(invalid)
    related = QuerySet(SerializationOwner, session).select_related("detail")
    with pytest.raises(QueryStateError):
        related.union(related)
    compound = base.values("id").union(base.values("id"))
    for method in ["filter", "exclude"]:
        with pytest.raises(TypeError):
            getattr(compound, method)(object())
    with pytest.raises(TypeError):
        compound.order_by(1)
    with pytest.raises(ValueError):
        compound.offset(-1)
    with pytest.raises(QueryStateError):
        compound.filter(id=F("id"))
    with pytest.raises(QueryStateError):
        compound.filter(id__bogus=1)
    with pytest.raises(TypeError):
        compound._filter_result(
            SimpleNamespace(children=[("leaf", "", 1)], connector=Q.AND, negated=False),
            negate=False,
        )
    assert "UNION" in compound.sql()
    other = QuerySet(LifecycleRecord, session).values("id")
    monkeypatch.setattr(
        other.compiler,
        "compile",
        lambda state: select(LifecycleRecord.id, LifecycleRecord.name),
    )
    with pytest.raises(QueryStateError, match="number"):
        base.values("id").union(other)
    assert _python_result_type(SimpleNamespace()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_fetch", "count", "exists", "stream"])
async def test_compound_database_errors(lifecycle_sessions, monkeypatch, method):
    async with lifecycle_sessions() as session:
        query = compound_models(session)
        failure = RuntimeError("driver failed")
        monkeypatch.setattr(
            session,
            "stream" if method == "stream" else "execute",
            AsyncMock(side_effect=failure),
        )
        with pytest.raises(DatabaseError) as error:
            if method == "stream":
                await anext(query.stream())
            else:
                await getattr(query, method)()
        assert error.value.cause is failure
