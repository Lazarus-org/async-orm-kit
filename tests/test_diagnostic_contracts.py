"""Diagnostics, no-op observer templates, and callback isolation contracts."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import sqlite

from async_orm_kit import NPlusOneDetector, QueryDebugger, QueryProfiler, StringField
from async_orm_kit.exceptions import InvalidRelationshipValueError
from async_orm_kit.observers.mixin import ObserverMixin
from async_orm_kit.observers.model_observer import BaseModelObserver
from async_orm_kit.observers.queryset_observer import BaseQuerySetObserver
from async_orm_kit.query.lookups.resolver import ResolverContext


@pytest.mark.asyncio
async def test_observer_templates_allow_partial_implementations():
    mixin = ObserverMixin()
    model = BaseModelObserver()
    query = BaseQuerySetObserver()
    for event in ["pre_save", "post_save", "pre_delete", "post_delete"]:
        assert await getattr(model, event)(instance=None, created=True) is None
    for event in [
        "pre_stream",
        "post_stream",
        "pre_execute",
        "post_execute",
        "pre_bulk_create",
        "post_bulk_create",
        "pre_bulk_update",
        "post_bulk_update",
        "pre_bulk_delete",
        "post_bulk_delete",
    ]:
        assert await getattr(query, event)(queryset=None) is None
    assert await mixin._trigger_observers([object()], "absent") is None
    called = []
    assert (
        await mixin._trigger_observers(
            [SimpleNamespace(event=lambda **kwargs: called.append(kwargs))],
            "event",
            value=1,
        )
        is None
    )
    assert called == [{"value": 1}]


def test_contexts_and_string_field_dialects():
    base = ResolverContext(in_order_by=True)
    assert base.for_filter() == ResolverContext(mark_filter=True)
    assert base.for_annotation() == ResolverContext(in_annotation=True)
    assert base.with_negation().in_negation and base.with_or().in_or_context
    assert not base.in_negation
    for length in [None, 4]:
        result = StringField(max_length=length, min_length=1).load_dialect_impl(
            sqlite.dialect()
        )
        assert result.length == length
    error = InvalidRelationshipValueError("wrong relation", "value", "author")
    assert (
        error.value == "value"
        and error.field_name == "author"
        and str(error) == "wrong relation"
    )


def test_debugger_measurements_and_profiler_failure_isolation(caplog):
    engine = create_engine("sqlite:///:memory:")
    try:
        with pytest.raises(ValueError):
            QueryDebugger(slow_threshold_ms=-1)
        with pytest.raises(ValueError):
            QueryProfiler(engine, slow_threshold_ms=-1)

        def fail(record):
            raise RuntimeError("callback failed")

        profiler = QueryProfiler(engine, on_query=fail)
        profiler.attach()
        with engine.connect() as conn:
            assert conn.scalar(text("select 1")) == 1
        assert "callback failed" in caplog.text
        profiler._finish("select 1", SimpleNamespace())
        profiler._handle_error(SimpleNamespace())
        profiler.detach()
        profiler._uninstall()
        profiler = QueryProfiler(engine)
        profiler.attach()
        with profiler.capture() as capture:
            with engine.connect() as conn:
                conn.scalar(text("select 1"))
            profiler.detach()
            assert profiler._registered
        assert not profiler._registered and capture.total_queries == 1
        debugger = QueryDebugger()
        with debugger.measure_query("disabled"):
            pass
        debugger.enable()
        with debugger.measure_query("enabled"):
            pass
        debugger.attach(engine)
        debugger.attach(engine)
        debugger.detach()
        detector = NPlusOneDetector()
        detector.attach(engine)
        detector.attach(engine)
        detector.detach()
    finally:
        engine.dispose()
