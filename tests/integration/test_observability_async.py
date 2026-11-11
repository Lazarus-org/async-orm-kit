"""Native async/aiosqlite contract for query profiling (required in hosted CI)."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

pytest.importorskip("aiosqlite")

from sqlalchemy import Integer, String, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from async_orm_kit import Model, NPlusOneDetector, QueryDebugger, QueryProfiler


class ObservedItem(Model):
    __tablename__ = "advanced_stage5_observed_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    score: Mapped[int] = mapped_column(Integer)


@pytest_asyncio.fixture
async def database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'observability.sqlite3'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(ObservedItem.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                ObservedItem(id=1, title="Ada", score=10),
                ObservedItem(id=2, title="Grace", score=20),
                ObservedItem(id=3, title="Lin", score=30),
            ]
        )
        await session.commit()
    try:
        yield engine, factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_orm_queryset_is_profiled(database):
    engine, factory = database
    profiler = QueryProfiler(engine)
    with profiler.capture() as profile:
        async with factory() as session:
            found = await ObservedItem.objects.filter(session, score__gte=20).order_by(
                "id"
            )
            assert [item.title for item in found] == ["Grace", "Lin"]
    assert profile.total_queries == 1
    assert profile.failed_queries == 0
    assert profile.records[0].sql.upper().startswith("SELECT ")
    assert profile.records[0].row_count is None


@pytest.mark.asyncio
async def test_scoped_profiler_isolated_across_concurrent_async_sessions(database):
    engine, factory = database
    profiler = QueryProfiler(engine)

    async def work(queries):
        async with profiler.acapture() as profile:
            async with factory() as session:
                for _ in range(queries):
                    await session.execute(select(ObservedItem.id).limit(1))
                    await asyncio.sleep(0)
            return profile

    one, three = await asyncio.gather(work(1), work(3))
    assert one.total_queries == 1
    assert three.total_queries == 3
    assert one.records[0].sql == three.records[0].sql


@pytest.mark.asyncio
async def test_real_n_plus_one_patterns_and_legacy_detector(database):
    engine, factory = database
    profiler = QueryProfiler(engine)
    legacy = NPlusOneDetector(threshold=3)
    legacy.attach(engine)
    legacy.start_monitoring()
    try:
        with profiler.capture() as profile:
            async with factory() as session:
                for key in (1, 2, 3):
                    await session.execute(
                        select(ObservedItem).where(ObservedItem.id == key)
                    )
        assert profile.total_queries == 3
        assert len(profile.n_plus_one_report(threshold=3)["potential_n_plus_one"]) == 1
        assert len(legacy.report()["potential_n_plus_one"]) == 1
    finally:
        legacy.stop_monitoring()
        legacy.detach()


@pytest.mark.asyncio
async def test_async_query_debugger_and_failed_sql(database):
    engine, factory = database
    profiler = QueryProfiler(engine)
    debugger = QueryDebugger()
    debugger.enable()
    debugger.attach(engine)
    try:
        with profiler.capture() as profile:
            async with factory() as session:
                with pytest.raises(SQLAlchemyError):
                    await session.execute(text("SELECT * FROM missing_stage5_table"))
                await session.rollback()
        assert profile.total_queries == 1
        assert profile.failed_queries == 1
        assert debugger.get_stats()["total_queries"] == 1
        assert debugger.query_log[0]["success"] is False
    finally:
        debugger.detach()
