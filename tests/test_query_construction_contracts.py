"""Query construction boundary contracts and SQL projection guards."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Integer
from sqlalchemy.exc import SQLAlchemyError
from test_model_lifecycle_contracts import (
    LifecycleRecord,
    SerializationOwner,
    lifecycle_sessions as lifecycle_sessions,
)

from async_orm_kit import Count, F, Q, QuerySet, RowNumber, Sum, Value, Window
from async_orm_kit.exceptions import (
    DatabaseError,
    ExpressionTypeError,
    FilterError,
    QueryStateError,
)
from async_orm_kit.query.aggregations.base import Aggregate
from async_orm_kit.query.lookups.resolver import ResolverContext
from async_orm_kit.queryset.builder import QueryBuilder
from async_orm_kit.queryset.state import QueryState
from async_orm_kit.queryset.validators import QuerySetValidator


def test_queryset_empty_calls_and_guards():
    q = QuerySet(LifecycleRecord, object())
    assert q.filter() is q and q.exclude() is q and q.order_by() is q
    for call, error in [
        (lambda: q[-1], ValueError),
        (lambda: q[:1].filter(id=1), TypeError),
        (lambda: q.union(q, all=1), TypeError),
        (q.intersection, ValueError),
        (q.difference, ValueError),
        (lambda: q.cte(recursive=1), TypeError),
        (lambda: q.select_for_update().cte(), QueryStateError),
        (
            lambda: (
                QuerySet(SerializationOwner, object()).select_related("detail").cte()
            ),
            QueryStateError,
        ),
        (lambda: q.select_for_update(nowait=1), TypeError),
        (lambda: q.values("id").select_for_update(), ValueError),
        (lambda: q.values_list("id").select_for_update(), ValueError),
        (lambda: q.values_list("id").only("id"), QueryStateError),
        (lambda: q.order_by("id").select_for_update().seek_after(1), QueryStateError),
        (lambda: q.order_by("id", "id").seek_after(1, 1), QueryStateError),
        (
            lambda: q.annotate(copy=F("id")).order_by("copy", "id").seek_after(1, 1),
            QueryStateError,
        ),
    ]:
        with pytest.raises(error):
            call()
    assert (
        q.select_for_update(of=LifecycleRecord).state.for_update["of"]
        is LifecycleRecord
    )
    for mode in ["values", "values_list"]:
        selected = getattr(q, mode)("id")
        for field in ["", "-", "--id", "name"]:
            with pytest.raises(QueryStateError):
                selected.order_by(field)
        with pytest.raises(QueryStateError):
            selected.distinct("name")
        assert selected.distinct("id").state.distinct_on
    for flat, fields, error in [
        (1, ("id",), TypeError),
        (True, ("id", "name"), QueryStateError),
    ]:
        with pytest.raises(error):
            q.values_list(*fields, flat=flat)
    with pytest.raises(QueryStateError):
        q.having(total__gt=1)
    assert QuerySetValidator(q.state)._get_selected_fields() == set()


def test_builder_direct_validation_and_noop_contracts(monkeypatch):
    state = QueryState(model=LifecycleRecord)
    state.join_graph = None
    b = QueryBuilder(state)
    for method in ["filter", "exclude", "order_by", "only", "having"]:
        assert getattr(b, method)() is state
    for call, error in [
        (lambda: b.filter(1), TypeError),
        (lambda: b.exclude(1), TypeError),
        (lambda: b.exclude(Value(1)), TypeError),
        (lambda: b.exclude((Sum("score") + 0) > 1), FilterError),
        (lambda: b.order_by(1), ExpressionTypeError),
        (lambda: b.limit(-1), ValueError),
        (lambda: b.offset(True), ValueError),
        (lambda: b.values_list(flat=1), TypeError),
        (lambda: b.values_list("id", "name", flat=True), ValueError),
        (lambda: b.values_list(), ValueError),
        (lambda: b.having(1), TypeError),
        (lambda: b._split_annotation_lookup("missing__deep__field"), FilterError),
        (lambda: b.filter(score=Window(RowNumber(), order_by=["id"])), FilterError),
        (lambda: b.filter((Window(RowNumber(), order_by=["id"]) + 0) > 1), FilterError),
    ]:
        with pytest.raises(error):
            call()
    assert b.defer("name", "name").deferred_fields == ["name"]
    monkeypatch.setattr(
        b._field_resolver,
        "resolve_to_column",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("resolver failed")),
    )
    for method, args in [("filter", ()), ("exclude", ()), ("order_by", ("id",))]:
        with pytest.raises(FilterError) as error:
            getattr(b, method)(*args, **({"id": 1} if method != "order_by" else {}))
        assert isinstance(error.value.__cause__, RuntimeError)


def test_aggregate_options_repr_and_invalid_operands():
    b = QueryBuilder(QueryState(model=LifecycleRecord))
    resolver = b._field_resolver
    assert (
        "distinct=True" in repr(Sum("score", distinct=True))
        and repr(Sum("score")) == "Sum('score')"
    )
    assert "CAST" in str(Sum("score", output_field=Integer).resolve(resolver))
    with pytest.raises(TypeError):
        Sum("score", output_field=object()).resolve(resolver)
    with pytest.raises(TypeError):
        Sum(1).resolve(resolver)
    with pytest.raises(NotImplementedError):
        Aggregate("score").resolve(resolver)
    with pytest.raises(ValueError):
        Count("*", distinct=True).resolve(resolver)
    assert "CASE" in str(Count("*", filter=Q(score__gt=1)).resolve(resolver))
    with pytest.raises(FilterError):
        b._resolve_mixed_group_predicate(Q(id=1))
    assert b._append_q_predicate(Q(), ResolverContext()) is None
    b.annotate(total=Sum("score"), rank=Window(RowNumber(), order_by=["id"]))
    with pytest.raises(FilterError):
        b.having(rank__gt=1)
    with pytest.raises(FilterError):
        b.exclude(Value(True), total__gt=1)
    assert b.filter((Sum("score") + 0) > 1).having_conditions
    projected = QueryBuilder(QueryState(model=LifecycleRecord))
    projected.values_list("name")
    projected.annotate(total=Sum("score"))
    assert projected._state.group_by_fields


@pytest.mark.asyncio
async def test_queryset_cache_aggregate_validation_and_failures(
    lifecycle_sessions, monkeypatch
):
    async with lifecycle_sessions() as session:
        q = LifecycleRecord.objects.all(session)
        assert await q == [] and await q == []
        with pytest.raises(ValueError):
            await q.aggregate()
        with pytest.raises(ValueError):
            await q.aggregate(**{"bad-name": Sum("score")})
        with pytest.raises(TypeError):
            await q.aggregate(total=F("score"))
        monkeypatch.setattr(
            session, "execute", AsyncMock(side_effect=SQLAlchemyError("failure"))
        )
        with pytest.raises(DatabaseError):
            await q.aggregate(total=Sum("score"))
