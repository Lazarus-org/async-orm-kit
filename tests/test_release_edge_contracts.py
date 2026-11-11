"""Regression cases for optional integrations and remaining lifecycle boundaries."""

import builtins
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    create_engine,
    literal,
    select,
    text,
)
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship
from test_advanced_stage7 import Stage7CompositeParent
from test_model_lifecycle_contracts import (
    LifecycleRecord,
    lifecycle_sessions as lifecycle_sessions,
)
from test_relationship_planning import Phase4Post, Phase4User

from async_orm_kit import (
    Case,
    F,
    Model,
    NPlusOneDetector,
    OuterRef,
    Q,
    QueryProfiler,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from async_orm_kit.exceptions import (
    FilterError,
    ModelAttributeError,
    QueryStateError,
    RelationshipError,
)
from async_orm_kit.integrations.fastapi import (
    create_session_dependency,
    register_exception_handlers,
)
from async_orm_kit.models.meta import ModelMeta
from async_orm_kit.query.compiler import QueryCompiler
from async_orm_kit.query.lookups.correlated import _leaf, resolve_q
from async_orm_kit.query.lookups.parser import FilterParser
from async_orm_kit.query.lookups.resolver import FieldResolver, ResolverContext
from async_orm_kit.query.lookups.resolver.types import ParsedFieldKey
from async_orm_kit.query.observability import QueryRecord
from async_orm_kit.query.postgres.optimizer import PostgresOptimizer
from async_orm_kit.queryset.builder import QueryBuilder
from async_orm_kit.queryset.state import QueryState


class CompositeLink(Model):
    __tablename__ = "contract_composite_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    left: Mapped[int] = mapped_column(ForeignKey(Stage7CompositeParent.tenant))
    right: Mapped[str] = mapped_column(ForeignKey(Stage7CompositeParent.code))
    parent: Mapped[Stage7CompositeParent] = relationship(
        primaryjoin=lambda: (
            (CompositeLink.left == Stage7CompositeParent.tenant)
            & (CompositeLink.right == Stage7CompositeParent.code)
        )
    )


class MultiColumnRecord(Model):
    __tablename__ = "contract_multicolumn_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    pair = column_property(
        Column("first", Integer, nullable=False),
        Column("second", Integer, nullable=False),
    )


@pytest.mark.asyncio
async def test_request_rollback_failure_preserves_original_error(
    lifecycle_sessions, monkeypatch, caplog
):
    async with lifecycle_sessions() as session:
        await session.execute(text("select 1"))
        monkeypatch.setattr(
            session, "rollback", AsyncMock(side_effect=RuntimeError("rollback failed"))
        )
        dependency = create_session_dependency(lambda: session)
        generator = dependency()
        assert await anext(generator) is session
        failure = ValueError("request failed")
        with pytest.raises(ValueError) as error:
            await generator.athrow(failure)
        assert error.value is failure and "rollback failed" in caplog.text
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "fastapi.responses", None)
        with pytest.raises(ImportError, match="optional"):
            register_exception_handlers(object())


@pytest.mark.asyncio
async def test_model_no_change_partial_save_and_unknown_update(lifecycle_sessions):
    row = LifecycleRecord(name="one")
    assert row.update_from_dict({"unknown": 2}) is row and not hasattr(row, "unknown")
    async with lifecycle_sessions() as session:
        await row.save(session)
        await row.save(session, update_fields=["name"])
        assert row.name == "one"


def test_metaclass_mixin_and_duplicate_observers():
    observer = object()
    base = ModelMeta("UnmappedContractBase", (), {"model_observers": [observer]})
    child = ModelMeta("UnmappedContractChild", (base,), {"model_observers": [observer]})
    assert child._model_observers == [observer]


def test_remaining_expression_and_profiler_guards(monkeypatch):
    resolver = FieldResolver(QueryState(model=LifecycleRecord))
    with pytest.raises(FilterError):
        Case(When(Q(), then=1)).resolve(resolver)
    with pytest.raises(FilterError):
        Case(When(id=1, then=1), default=F("missing")).resolve(resolver)
    assert "CASE" in str(Case(When(id=1, then=1)).resolve(resolver))
    assert (F("score") + Value(1))._resolve_operand(Value(2), resolver).value == 2
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(TypeError):
            QueryProfiler(engine, on_query=1)
        profiler = QueryProfiler(engine)
        context = SimpleNamespace()
        profiler._before_cursor_execute(None, None, "select 1", None, context, False)
        assert not hasattr(context, profiler._clock_key)
        debugger = NPlusOneDetector()
        debugger._record_event(QueryRecord("bad SQL", 0, 0, None, False))
        assert debugger.query_count == 0
    finally:
        engine.dispose()


def test_correlated_relationship_validation_and_empty_nested_q(monkeypatch):
    resolver = FieldResolver(QueryState(model=Phase4User))
    context = ResolverContext()
    assert resolve_q(Q(Q()), resolver, context) is None
    original = resolver.parse_field_key
    monkeypatch.setattr(
        resolver,
        "parse_field_key",
        lambda key: ParsedFieldKey(
            relationship_parts=["missing"],
            field_name="id",
            lookup="exact",
            is_simple=False,
        ),
    )
    with pytest.raises(ModelAttributeError):
        _leaf("missing__id", 1, resolver, context)
    monkeypatch.setattr(resolver, "parse_field_key", original)
    with pytest.raises(ModelAttributeError):
        _leaf("posts__missing", 1, resolver, context)
    for value in [None, 1]:
        if value is None:
            assert "EXISTS" in str(_leaf("posts__author", value, resolver, context))
        else:
            with pytest.raises(FilterError):
                _leaf("posts__author", value, resolver, context)
    composite = FieldResolver(QueryState(model=CompositeLink))
    with pytest.raises(ModelAttributeError):
        _leaf("parent__pk", 1, composite, context)
    with pytest.raises(ValueError):
        Sum("parent").resolve(composite)
    with pytest.raises(ValueError):
        PostgresOptimizer.lateral_join_top_n(
            Stage7CompositeParent, LifecycleRecord, "id", "id"
        )


def test_builder_empty_q_mixed_filters_and_errors(monkeypatch):
    b = QueryBuilder(QueryState(model=LifecycleRecord))
    assert b.filter(Q(Q())).filters == [] and b.exclude(Q()).filters == []
    b.annotate(total=Sum("score"))
    assert b.exclude(total__gt=1).having_conditions
    assert b._resolve_mixed_group_predicate(Q()) is None
    for method in ["filter", "exclude"]:
        with monkeypatch.context() as patch:
            patch.setattr(
                FilterParser,
                "parse",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    RuntimeError("handler failed")
                ),
            )
            with pytest.raises(FilterError) as error:
                getattr(b, method)(id=1)
            assert isinstance(error.value.__cause__, RuntimeError)
    related = QueryBuilder(QueryState(model=Phase4Post))
    related.select_related("author", "author")
    assert related._state.select_related == ["author"]


def test_compiler_corrupt_graph_and_sqlalchemy20_compatibility(monkeypatch):
    compiler = QueryCompiler()
    state = QueryState(model=LifecycleRecord)
    state.join_graph._join_order.append(SimpleNamespace(parent=None))
    with pytest.raises(RelationshipError):
        compiler._apply_joins(select(LifecycleRecord), state)
    original = builtins.__import__

    def import20(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sqlalchemy.dialects.postgresql" and "distinct_on" in fromlist:
            raise ImportError("SQLAlchemy 2.0")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import20)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        q = QuerySet(LifecycleRecord, object()).distinct("id")
        assert compiler._apply_distinct(select(LifecycleRecord), q.state)._distinct_on


@pytest.mark.asyncio
async def test_multi_column_mutation_and_keyset_rejections(lifecycle_sessions):
    async with lifecycle_sessions() as session:
        q = QuerySet(MultiColumnRecord, session)
        with pytest.raises(QueryStateError):
            q.order_by("pair", "id").seek_after(1, 1)
        with pytest.raises(ModelAttributeError):
            await q.bulk_create([{"pair": 1}])


def test_expression_known_errors_and_deep_self_correlation(monkeypatch):
    from async_orm_kit.query.expressions.functions import Lower

    resolver = FieldResolver(QueryState(model=LifecycleRecord))
    failure = FilterError("invalid conditional predicate")
    with monkeypatch.context() as patch:
        patch.setattr(
            When, "resolve", lambda *args, **kwargs: (_ for _ in ()).throw(failure)
        )
        with pytest.raises(FilterError) as error:
            Case(When(id=1, then=1)).resolve(resolver)
        assert error.value is failure
    with monkeypatch.context() as patch:
        patch.setattr(
            F, "resolve", lambda *args, **kwargs: (_ for _ in ()).throw(failure)
        )
        with pytest.raises(FilterError) as error:
            Case(When(id=1, then=1), default=F("name")).resolve(resolver)
        assert error.value is failure
    assert "lower" in str((F("name") + Lower("name")).resolve(resolver))
    assert "score" in str((F("score") + literal(1)).resolve(resolver))
    q = (
        QuerySet(LifecycleRecord, object())
        .filter(id=OuterRef(OuterRef("id")))
        .values("id")
    )
    resolved = Subquery(q).resolve(resolver)
    from sqlalchemy.sql import visitors

    from async_orm_kit.query.expressions.subquery import _OUTER_REF

    assert any(
        element._annotations.get(_OUTER_REF) == ("id", 1)
        for element in visitors.iterate(resolved)
    )
    builder = QueryBuilder(QueryState(model=LifecycleRecord))
    builder.annotate(total=Sum("score"), copy=F("score"))
    builder.filter(copy=Sum("score"))
    assert builder._state.having_conditions
    query = (
        QuerySet(Phase4User, object())
        .annotate(post_id=F("posts__id"))
        .order_by("id")
        .seek_after(1)
    )
    assert query.state.seek_active
    assert QuerySet(LifecycleRecord, object()).having().state.having_conditions == []
    graph = QueryState(model=Phase4User).join_graph
    node = graph.add_node("posts")
    node.parent.children.clear()
    node.parent = None
    assert graph.remove_node("posts") and not graph.paths


@pytest.mark.asyncio
async def test_stream_cancellation_during_acquisition(lifecycle_sessions, monkeypatch):
    import asyncio

    async with lifecycle_sessions() as session:
        q = LifecycleRecord.objects.all(session)
        combined = q.union(q)
        monkeypatch.setattr(
            session, "stream", AsyncMock(side_effect=asyncio.CancelledError())
        )
        for iterator in [q.stream(), combined.stream()]:
            with pytest.raises(asyncio.CancelledError):
                await anext(iterator)
