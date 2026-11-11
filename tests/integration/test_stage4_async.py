"""Native SQLAlchemy AsyncSession contract for extensibility and deferred fields."""

from __future__ import annotations

import pytest
import pytest_asyncio

pytest.importorskip("aiosqlite")

from sqlalchemy import Integer, String, Text, func, inspect as sa_inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from async_orm_kit import AggregateFunction, F, Function, Model


class AsyncExtItem(Model):
    __tablename__ = "stage4_async_ext_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    group: Mapped[str] = mapped_column(String(20))
    score: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage4.sqlite3'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda connection: AsyncExtItem.__table__.create(connection)
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                AsyncExtItem(id=1, group="a", score=10, note="large attachment"),
                AsyncExtItem(id=2, group="a", score=10, note="another attachment"),
                AsyncExtItem(id=3, group="b", score=20, note=None),
            ]
        )
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_deferred_async_column_explicit_refresh(session_factory):
    async with session_factory() as session:
        records = await AsyncExtItem.objects.defer(session, "note").order_by("id")
        assert "note" in sa_inspect(records[0]).unloaded
        assert records[0].id == 1
        await session.refresh(records[0], ["note"])
        assert records[0].note == "large attachment"


@pytest.mark.asyncio
async def test_native_keyset_pages_do_not_skip_ties(session_factory):
    async with session_factory() as session:
        root = AsyncExtItem.objects.all(session).order_by("score", "id")
        first = await root.limit(2)
        assert [item.id for item in first] == [1, 2]
        second = await root.seek_after(first[-1].score, first[-1].id).limit(2)
        assert [item.id for item in second] == [3]


@pytest.mark.asyncio
async def test_custom_lookups_and_functions_execute_with_async_session(session_factory):
    async with session_factory() as session:
        items = await (
            AsyncExtItem.objects.all(session)
            .with_lookup("multiple_of", lambda col, amount: col % amount == 0)
            .filter(score__multiple_of=20)
            .order_by("id")
        )
        assert [item.id for item in items] == [3]

        texts = (
            await AsyncExtItem.objects.annotate(
                session,
                letter=Function(lambda col: func.substr(col, 1, 1), F("group")),
            )
            .values("id", "letter")
            .order_by("id")
        )
        assert texts == [
            {"id": 1, "letter": "a"},
            {"id": 2, "letter": "a"},
            {"id": 3, "letter": "b"},
        ]

        grouped = (
            await AsyncExtItem.objects.values(session, "group")
            .annotate(total=AggregateFunction("score", lambda col: func.total(col)))
            .order_by("group")
        )
        assert grouped == [{"group": "a", "total": 20.0}, {"group": "b", "total": 20.0}]
