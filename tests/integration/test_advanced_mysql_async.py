"""Live MySQL 8.4/asyncmy advanced SQL contract against a disposable database."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

URL = os.getenv("ASYNC_ORM_KIT_MYSQL_URL")
if not URL:
    pytest.skip("ASYNC_ORM_KIT_MYSQL_URL is required", allow_module_level=True)

pytest.importorskip("asyncmy")

from sqlalchemy import JSON, Integer, String  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Mapped, mapped_column  # noqa: E402

from async_orm_kit import F, JSONPath, Model, RowNumber, Window  # noqa: E402


class AdvancedMySQLRow(Model):
    __tablename__ = "stage6_advanced_mysql_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


@pytest_asyncio.fixture
async def mysql_factory():
    url = make_url(URL)
    if url.drivername != "mysql+asyncmy" or not (url.database or "").endswith("_test"):
        raise RuntimeError(
            "Live advanced MySQL tests require a disposable *_test database"
        )
    engine = create_async_engine(URL, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(AdvancedMySQLRow.__table__.drop, checkfirst=True)
            await conn.run_sync(AdvancedMySQLRow.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add_all(
                    [
                        AdvancedMySQLRow(
                            id=1, category="a", score=10, payload={"age": 21}
                        ),
                        AdvancedMySQLRow(
                            id=2, category="a", score=20, payload={"age": 30}
                        ),
                        AdvancedMySQLRow(
                            id=3, category="b", score=30, payload={"age": 40}
                        ),
                    ]
                )
        yield factory
    finally:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(AdvancedMySQLRow.__table__.drop, checkfirst=True)
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_live_mysql_nested_json_cast_and_filter(mysql_factory):
    async with mysql_factory() as session:
        data = await (
            AdvancedMySQLRow.objects.annotate(
                session, age=JSONPath(F("payload"), "age", as_type="integer")
            )
            .filter(age__gte=30)
            .values("id", "age")
            .order_by("id")
        )
        assert data == [{"id": 2, "age": 30}, {"id": 3, "age": 40}]


@pytest.mark.asyncio
async def test_live_mysql_window_ranking_and_compound_queries(mysql_factory):
    async with mysql_factory() as session:
        data = await (
            AdvancedMySQLRow.objects.annotate(
                session,
                position=Window(
                    RowNumber(),
                    partition_by=[F("category")],
                    order_by=[F("score").desc()],
                ),
            )
            .values("id", "position")
            .order_by("id")
        )
        assert data == [
            {"id": 1, "position": 2},
            {"id": 2, "position": 1},
            {"id": 3, "position": 1},
        ]
        first = AdvancedMySQLRow.objects.filter(session, score__lte=20).values_list(
            "id", flat=True
        )
        second = AdvancedMySQLRow.objects.filter(session, score__gte=20).values_list(
            "id", flat=True
        )
        assert await first.union(second).order_by("id") == [1, 2, 3]
        assert await first.intersection(second) == [2]
        assert await first.difference(second) == [1]
        assert await first.union(second, all=True).count() == 4


@pytest.mark.asyncio
async def test_live_mysql_write_rollback_and_session_owned_transactions(mysql_factory):
    async with mysql_factory() as session:
        async with session.begin():
            changed = await AdvancedMySQLRow.objects.filter(session, id=1).update(
                score=F("score") + 5
            )
            assert changed == 1
            row = await AdvancedMySQLRow.objects.get(session, id=1)
            await session.refresh(row)
            assert row.score == 15
            await session.rollback()
    async with mysql_factory() as session:
        assert (await AdvancedMySQLRow.objects.get(session, id=1)).score == 10
