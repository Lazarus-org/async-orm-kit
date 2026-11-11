"""Live PostgreSQL 16/asyncpg advanced-expression contract (disposable DB only).

The provider CI job must run this module with a real PostgreSQL server; no mocked
connection or SQL compilation result is a substitute for these assertions.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

URL = os.getenv("ASYNC_ORM_KIT_POSTGRES_URL")
if not URL:
    pytest.skip("ASYNC_ORM_KIT_POSTGRES_URL is required", allow_module_level=True)

pytest.importorskip("asyncpg")

from sqlalchemy import Integer, String  # noqa: E402
from sqlalchemy.dialects.postgresql import ARRAY, JSONB  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Mapped, mapped_column  # noqa: E402

from async_orm_kit import (  # noqa: E402
    ArrayContains,
    ArrayIndex,
    ArrayLength,
    ArrayOverlap,
    F,
    JSONContains,
    JSONHasKey,
    JSONPath,
    Model,
    RowNumber,
    Window,
)


class AdvancedPGRow(Model):
    __tablename__ = "stage6_advanced_postgres_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False)


@pytest_asyncio.fixture
async def pg_factory():
    url = make_url(URL)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").endswith(
        "_test"
    ):
        raise RuntimeError(
            "Live advanced PostgreSQL tests require a disposable *_test database"
        )
    engine = create_async_engine(URL, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(AdvancedPGRow.__table__.drop, checkfirst=True)
            await connection.run_sync(AdvancedPGRow.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add_all(
                    [
                        AdvancedPGRow(
                            id=1,
                            group_id=1,
                            score=10,
                            payload={"user": {"age": 18}, "active": True},
                            tags=["python", "api"],
                        ),
                        AdvancedPGRow(
                            id=2,
                            group_id=1,
                            score=20,
                            payload={"user": {"age": 25}, "active": False},
                            tags=["orm", "sql"],
                        ),
                        AdvancedPGRow(
                            id=3,
                            group_id=2,
                            score=30,
                            payload={"user": {"age": 40}, "active": True},
                            tags=[],
                        ),
                    ]
                )
        yield factory
    finally:
        try:
            async with engine.begin() as connection:
                await connection.run_sync(AdvancedPGRow.__table__.drop, checkfirst=True)
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_live_jsonb_path_containment_and_key_existence(pg_factory):
    async with pg_factory() as session:
        selected = await (
            AdvancedPGRow.objects.annotate(
                session,
                age=JSONPath(F("payload"), "user", "age", as_type="integer"),
                contains=JSONContains(F("payload"), {"active": True}),
                has_user=JSONHasKey(F("payload"), "user"),
            )
            .filter(contains=True, has_user=True, age__gte=20)
            .values_list("id", flat=True)
            .order_by("id")
        )
        assert selected == [3]


@pytest.mark.asyncio
async def test_live_postgres_array_operators_and_one_based_indexes(pg_factory):
    async with pg_factory() as session:
        rows = await (
            AdvancedPGRow.objects.annotate(
                session,
                length=ArrayLength(F("tags")),
                first=ArrayIndex(F("tags"), 1),
                overlaps=ArrayOverlap(F("tags"), ["sql"]),
                contains=ArrayContains(F("tags"), ["python"]),
            )
            .values("id", "length", "first", "overlaps", "contains")
            .order_by("id")
        )
        assert rows == [
            {
                "id": 1,
                "length": 2,
                "first": "python",
                "overlaps": False,
                "contains": True,
            },
            {"id": 2, "length": 2, "first": "orm", "overlaps": True, "contains": False},
            {
                "id": 3,
                "length": None,
                "first": None,
                "overlaps": False,
                "contains": False,
            },
        ]


@pytest.mark.asyncio
async def test_live_postgres_window_set_and_row_lock(pg_factory):
    async with pg_factory() as session:
        ranks = await (
            AdvancedPGRow.objects.annotate(
                session,
                rank=Window(
                    RowNumber(),
                    partition_by=[F("group_id")],
                    order_by=[F("score").desc()],
                ),
            )
            .values("id", "rank")
            .order_by("id")
        )
        assert ranks == [
            {"id": 1, "rank": 2},
            {"id": 2, "rank": 1},
            {"id": 3, "rank": 1},
        ]

        first = AdvancedPGRow.objects.filter(session, score__lte=20).values_list(
            "id", flat=True
        )
        second = AdvancedPGRow.objects.filter(session, score__gte=20).values_list(
            "id", flat=True
        )
        assert await first.union(second).order_by("id") == [1, 2, 3]
        assert await first.intersection(second) == [2]
        assert await first.difference(second) == [1]
        assert await first.union(second, all=True).count() == 4

        locked = (
            await AdvancedPGRow.objects.filter(session, id=1)
            .select_for_update()
            .first()
        )
        assert locked is not None and locked.id == 1
        await session.rollback()
