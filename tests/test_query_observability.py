"""Stage 5 cursor-level observability regressions with real SQLite SQL execution."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from async_orm_kit import NPlusOneDetector, QueryDebugger, QueryProfiler
from async_orm_kit.query.observability import sanitized_sql, sql_fingerprint


@pytest.fixture
def engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    people = Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            people.insert(),
            [
                {"id": 1, "name": "Ada"},
                {"id": 2, "name": "Grace"},
                {"id": 3, "name": "Lin"},
            ],
        )
    yield engine
    engine.dispose()


def test_profiler_records_real_sql_and_cursor_rows(engine):
    profiler = QueryProfiler(engine)
    with profiler.capture() as profile:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE people SET name = :value WHERE id = :id"),
                {"value": "secret-name", "id": 1},
            )
            result = conn.execute(
                text("SELECT name FROM people WHERE id = :id"), {"id": 1}
            )
            assert result.scalar_one() == "secret-name"
    assert profile.total_queries == 2
    assert profile.records[0].row_count == 1
    assert (
        profile.records[1].row_count is None
    )  # SELECT rowcount isn't known at cursor execute
    assert all(record.success for record in profile.records)
    assert all(record.duration_ms >= 0 for record in profile.records)
    assert "secret-name" not in str(profile.records)
    assert not event.contains(engine, "before_cursor_execute", profiler._before_cb)
    assert profile.stats()["failed_queries"] == 0


def test_profiler_records_dbapi_errors_and_does_not_change_exception(engine):
    profiler = QueryProfiler(engine)
    with profiler.capture() as profile:
        with engine.connect() as conn:
            with pytest.raises(OperationalError):
                conn.execute(
                    text("SELECT name FROM does_not_exist WHERE id = :id"), {"id": 93}
                )
    assert profile.total_queries == 1
    assert profile.failed_queries == 1
    assert profile.records[0].success is False
    assert profile.records[0].error_type == "OperationalError"
    assert profile.records[0].row_count is None
    assert "93" not in profile.records[0].sql
    assert profile.stats()["failed_queries"] == 1


def test_profiler_caps_history_but_preserves_totals(engine):
    profiler = QueryProfiler(engine, max_entries=2, slow_threshold_ms=0)
    with profiler.capture() as profile:
        with engine.connect() as conn:
            for value in (1, 2, 3, 4):
                conn.execute(text("SELECT :value AS v"), {"value": value}).scalar()
    assert profile.total_queries == 4
    assert len(profile.records) == 2
    assert profile.dropped_entries == 2
    assert profile.slow_queries == 4
    assert profile.stats()["total_queries"] == 4
    assert profile.stats()["dropped_entries"] == 2


def test_profiler_captures_nested_scopes_and_removes_listener_after_outer_exit(engine):
    profiler = QueryProfiler(engine)
    with profiler.capture() as outer:
        with profiler.capture() as inner:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        with engine.connect() as conn:
            conn.execute(text("SELECT 2"))
        assert outer.total_queries == 2
        assert inner.total_queries == 1
        assert event.contains(engine, "after_cursor_execute", profiler._after_cb)
    assert not event.contains(engine, "after_cursor_execute", profiler._after_cb)


def test_profiler_manual_attach_detach_is_idempotent(engine):
    profiler = QueryProfiler(engine)
    profiler.attach()
    profiler.attach()
    assert event.contains(engine, "before_cursor_execute", profiler._before_cb)
    with profiler.capture() as profile:
        profiler.detach()  # cannot detach an active capture's listeners
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        assert profile.total_queries == 1
    assert not event.contains(engine, "before_cursor_execute", profiler._before_cb)


def test_profiler_captures_are_isolated_between_async_tasks(engine):
    profiler = QueryProfiler(engine)

    async def work(number: int):
        async with profiler.acapture() as profile:
            await asyncio.sleep(0)
            with engine.connect() as conn:
                for _ in range(number):
                    conn.execute(text("SELECT :value"), {"value": number})
            await asyncio.sleep(0)
            return profile

    first, second = asyncio.run(_run_both(work))
    assert first.total_queries == 1
    assert second.total_queries == 3
    assert not event.contains(engine, "before_cursor_execute", profiler._before_cb)


async def _run_both(work):
    return await asyncio.gather(work(1), work(3))


def test_profiler_capture_preserves_task_local_context_under_threaded_engine_use():
    # Return/close each thread's connection inside its owning worker.
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=NullPool)
    profiler = QueryProfiler(engine)
    profiler.attach()  # never install/uninstall listeners during concurrent SQL

    def work(count):
        with profiler.capture() as capture:
            with engine.connect() as conn:
                for i in range(count):
                    conn.execute(text("SELECT :value"), {"value": i})
            return capture

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(work, 2)
        second = pool.submit(work, 4)
        assert first.result().total_queries == 2
        assert second.result().total_queries == 4
    profiler.detach()
    engine.dispose()


def test_n_plus_one_profile_detects_only_repeated_select_where(engine):
    profiler = QueryProfiler(engine)
    with profiler.capture() as profile:
        with engine.connect() as conn:
            for key in (1, 2, 3, 1):
                conn.execute(
                    text("SELECT id FROM people WHERE id = :id"), {"id": key}
                ).fetchall()
            for _ in range(4):
                conn.execute(text("SELECT 1"))
            conn.execute(text("UPDATE people SET name='Ada' WHERE id=1"))
    report = profile.n_plus_one_report(threshold=3)
    assert report["total_queries"] == 9
    assert len(report["potential_n_plus_one"]) == 1
    assert report["potential_n_plus_one"][0]["count"] == 4
    assert "people WHERE id" in report["potential_n_plus_one"][0]["pattern"]
    with pytest.raises(ValueError):
        profile.n_plus_one_report(threshold=1)


def test_legacy_debugger_automatically_records_events(engine):
    debugger = QueryDebugger(max_entries=2, slow_threshold_ms=0)
    debugger.enable()
    debugger.attach(engine)
    debugger.attach(engine)
    try:
        with engine.connect() as conn:
            for value in (1, 2, 3):
                conn.execute(text("SELECT :value"), {"value": value})
        assert len(debugger.query_log) == 2
        assert debugger.get_stats()["total_queries"] == 2
        assert all("sql" in event for event in debugger.query_log)
        debugger.disable()
        with engine.connect() as conn:
            conn.execute(text("SELECT 4"))
        assert len(debugger.query_log) == 2
        debugger.clear()
        assert debugger.get_stats()["total_queries"] == 0
        callback = debugger._profiler._before_cb
    finally:
        debugger.detach()
    assert not event.contains(engine, "before_cursor_execute", callback)


def test_legacy_n_plus_one_detector_is_event_backed_and_resettable(engine):
    detector = NPlusOneDetector(threshold=3)
    detector.attach(engine)
    try:
        detector.start_monitoring()
        with engine.connect() as conn:
            for key in (1, 2, 3):
                conn.execute(
                    text("SELECT name FROM people WHERE id=:id"), {"id": key}
                ).fetchall()
            conn.execute(text("SELECT 1"))
        report = detector.report()
        assert report["total_queries"] == 4
        assert len(report["potential_n_plus_one"]) == 1
        detector.stop_monitoring()
        with engine.connect() as conn:
            conn.execute(text("SELECT 2"))
        assert detector.report()["total_queries"] == 4
        detector.start_monitoring()
        assert detector.report()["total_queries"] == 0
    finally:
        detector.detach()


def test_monitor_callback_failure_does_not_break_query_execution(engine):
    def broken(_record):
        raise RuntimeError("bad monitoring callback")

    profiler = QueryProfiler(engine, on_query=broken)
    profiler.attach()
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 7")).scalar_one() == 7
    finally:
        profiler.detach()


def test_monitors_can_be_installed_on_same_engine_without_cross_cleanup(engine):
    first = QueryProfiler(engine)
    second = QueryProfiler(engine)
    with first.capture() as a:
        with second.capture() as b:
            with engine.connect() as conn:
                conn.execute(text("SELECT 5"))
        with engine.connect() as conn:
            conn.execute(text("SELECT 6"))
    assert a.total_queries == 2
    assert b.total_queries == 1


def test_sql_redaction_and_fingerprinting_preserve_identifier_digits():
    sql = "SELECT order2, 'email@example.com', 1234 FROM user2 WHERE id2 = 71 -- customer 123"
    safe = sanitized_sql(sql)
    assert "email@example.com" not in safe
    assert "1234" not in safe
    assert "customer" not in safe
    assert "order2" in safe and "user2" in safe and "id2" in safe
    assert sql_fingerprint("SELECT id2 FROM user2 WHERE id2 = 1") == sql_fingerprint(
        "SELECT id2 FROM user2 WHERE id2 = 2"
    )


def test_invalid_profiler_configuration(engine):
    with pytest.raises(TypeError):
        QueryProfiler(object())
    with pytest.raises(ValueError):
        QueryProfiler(engine, max_entries=0)
    with pytest.raises(ValueError):
        QueryProfiler(engine, slow_threshold_ms=-1)
    with pytest.raises(ValueError):
        NPlusOneDetector(threshold=1)
    with pytest.raises(ValueError):
        QueryDebugger(max_entries=0)


def test_pattern_memory_is_bounded_even_with_many_unique_sql_templates(engine):
    profiler = QueryProfiler(engine, max_entries=2)
    with profiler.capture() as profile:
        with engine.connect() as conn:
            for value in ("alpha", "beta", "gamma", "delta"):
                conn.execute(text(f"SELECT 1 AS column_{value}"))
    assert len(profile.patterns) == 2
    assert profile.untracked_patterns == 2
    assert profile.n_plus_one_report(threshold=2)["untracked_patterns"] == 2


def test_cancelled_capture_cleans_up_event_listeners(engine):
    profiler = QueryProfiler(engine)

    async def worker():
        async with profiler.acapture() as profile:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            await asyncio.sleep(100)
        return profile

    async def run():
        task = asyncio.create_task(worker())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not event.contains(engine, "before_cursor_execute", profiler._before_cb)


def test_inherited_closed_context_does_not_append_to_old_capture(engine):
    profiler = QueryProfiler(engine)

    async def run():
        gate = asyncio.Event()

        async def inherited_task():
            await gate.wait()
            with engine.connect() as conn:
                conn.execute(text("SELECT 100"))

        with profiler.capture() as profile:
            spawned = asyncio.create_task(inherited_task())
        with profiler.capture() as fresh:
            gate.set()
            await spawned
            assert fresh.total_queries == 0
        return profile

    profile = asyncio.run(run())
    assert profile.total_queries == 0
