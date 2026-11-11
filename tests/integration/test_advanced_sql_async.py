"""Stage 3 extended-SQL contract against real SQLAlchemy AsyncSession/aiosqlite."""

from __future__ import annotations

import pytest
import pytest_asyncio

pytest.importorskip("aiosqlite")

from sqlalchemy import JSON, Integer, String, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Mapped, mapped_column  # noqa: E402

from async_orm_kit import F, JSONPath, Model  # noqa: E402


class ExtendedSQLRecord(Model):
    __tablename__ = "advanced_sql_async_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    group_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage3.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Model.metadata.create_all(
                sync, tables=[ExtendedSQLRecord.__table__]
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    ExtendedSQLRecord(
                        name="Ada",
                        group_id=1,
                        payload={"account": {"tier": "gold", "points": 5}},
                    ),
                    ExtendedSQLRecord(
                        name="Grace",
                        group_id=1,
                        payload={"account": {"tier": "silver", "points": 8}},
                    ),
                    ExtendedSQLRecord(
                        name="Linus",
                        group_id=2,
                        payload={"account": {"tier": "gold", "points": 12}},
                    ),
                ]
            )
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_native_async_json_path_filter_and_typed_projection(factory):
    async with factory() as session:
        query = (
            ExtendedSQLRecord.objects.annotate(
                session,
                points=JSONPath(F("payload"), "account", "points", as_type="integer"),
            )
            .filter(points__gte=8)
            .values("name", "points")
            .order_by("points")
        )
        assert await query == [
            {"name": "Grace", "points": 8},
            {"name": "Linus", "points": 12},
        ]


@pytest.mark.asyncio
async def test_native_async_union_intersect_except_and_count(factory):
    async with factory() as session:
        first = ExtendedSQLRecord.objects.filter(session, group_id=1).values_list(
            "name", flat=True
        )
        second = ExtendedSQLRecord.objects.filter(
            session, name__in=["Ada", "Linus"]
        ).values_list("name", flat=True)

        assert await first.union(second).order_by("name") == ["Ada", "Grace", "Linus"]
        assert await first.intersection(second) == ["Ada"]
        assert await first.difference(second) == ["Grace"]
        assert await first.union(second, all=True).count() == 4


@pytest.mark.asyncio
async def test_native_async_query_cte_and_sqlalchemy_recursive_cte(factory):
    async with factory() as session:
        scores = (
            ExtendedSQLRecord.objects.annotate(
                session,
                score=JSONPath(F("payload"), "account", "points", as_type="integer"),
            )
            .values_list("score", flat=True)
            .cte("scores")
        )
        total = await session.scalar(select(func.sum(scores.c.score)))
        assert total == 25

        recursive = (
            ExtendedSQLRecord.objects.filter(session, id=1)
            .values_list("id", flat=True)
            .cte("ids", recursive=True)
        )
        recursive = recursive.union_all(
            select(ExtendedSQLRecord.id)
            .join(recursive, ExtendedSQLRecord.id == recursive.c.id + 1)
            .where(ExtendedSQLRecord.id <= 3)
        )
        ids = (
            await session.scalars(select(recursive.c.id).order_by(recursive.c.id))
        ).all()
        assert ids == [1, 2, 3]


@pytest.mark.asyncio
async def test_native_async_model_set_result_uses_identity_map(factory):
    async with factory() as session:
        source = ExtendedSQLRecord.objects.all(session)
        first = ExtendedSQLRecord.objects.filter(session, group_id=1)
        second = ExtendedSQLRecord.objects.filter(session, group_id=2)
        combined = first.union(second).order_by("id")
        rows = await combined
        assert [row.name for row in rows] == ["Ada", "Grace", "Linus"]
        again = await source
        assert all(row is other for row, other in zip(rows, again, strict=False))


@pytest.mark.asyncio
async def test_native_async_set_stream_closes_early_and_session_survives(factory):
    async with factory() as session:
        first = ExtendedSQLRecord.objects.filter(session, group_id=1).values_list(
            "name", flat=True
        )
        second = ExtendedSQLRecord.objects.filter(session, group_id=2).values_list(
            "name", flat=True
        )
        iterator = first.union(second).order_by("name").stream()
        assert await anext(iterator) == "Ada"
        await iterator.aclose()
        assert await first.union(second).count() == 3
