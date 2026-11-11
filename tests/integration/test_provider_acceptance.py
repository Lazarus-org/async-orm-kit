"""Shared native SQL acceptance suite; defaults to disposable in-memory SQLite.

CI sets ASYNC_ORM_KIT_ACCEPTANCE_URL to its dedicated PostgreSQL/MySQL test DB.
Only this module's tables are created/dropped, and external DB names must end
in _test. No existing application database is an acceptable test target.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import aclosing

import pytest
import pytest_asyncio
from sqlalchemy import JSON, ForeignKey, String, literal, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

from async_orm_kit import Count, Exists, F, Model, OuterRef, Q, Subquery
from async_orm_kit.exceptions import DatabaseError


class AcceptanceParent(Model):
    __tablename__ = "release_acceptance_parents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    score: Mapped[int] = mapped_column(default=0)
    payload: Mapped[dict] = mapped_column(JSON)
    children: Mapped[list[AcceptanceChild]] = relationship(
        back_populates="parent", lazy="raise"
    )


class AcceptanceChild(Model):
    __tablename__ = "release_acceptance_children"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("release_acceptance_parents.id"))
    parent: Mapped[AcceptanceParent] = relationship(
        back_populates="children", lazy="raise"
    )


@pytest_asyncio.fixture
async def acceptance():
    url = make_url(
        os.getenv("ASYNC_ORM_KIT_ACCEPTANCE_URL", "sqlite+aiosqlite:///:memory:")
    )
    if url.get_backend_name() != "sqlite" and not (url.database or "").endswith(
        "_test"
    ):
        raise ValueError(
            "Native acceptance requires a disposable database ending in _test"
        )
    engine = create_async_engine(url)
    tables = [AcceptanceParent.__table__, AcceptanceChild.__table__]
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: Model.metadata.create_all(sync, tables=tables)
            )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as session:
            session.add_all(
                [
                    AcceptanceParent(
                        id=1,
                        name="one",
                        score=10,
                        payload={"user": {"age": 20}, "active": True},
                        children=[AcceptanceChild(id=1), AcceptanceChild(id=2)],
                    ),
                    AcceptanceParent(
                        id=2,
                        name="two",
                        score=20,
                        payload={"user": {"age": 30}, "active": False},
                        children=[AcceptanceChild(id=3)],
                    ),
                    AcceptanceParent(
                        id=3,
                        name="three",
                        score=30,
                        payload={"user": {"age": 40}, "active": True},
                    ),
                ]
            )
        yield factory
    finally:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(
                    lambda sync: Model.metadata.drop_all(sync, tables=tables)
                )
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_native_correlation_and_scalar_subquery(acceptance):
    async with acceptance() as session:
        inner = AcceptanceChild.objects.filter(session, parent_id=OuterRef("id"))
        rows = await AcceptanceParent.objects.filter(session, Exists(inner)).order_by(
            "id"
        )
        assert [row.id for row in rows] == [1, 2]
        first_child = inner.order_by("id").values_list("id", flat=True).limit(1)
        rows = (
            await AcceptanceParent.objects.annotate(
                session, child=Subquery(first_child)
            )
            .values("id", "child")
            .order_by("id")
        )
        assert rows == [
            {"id": 1, "child": 1},
            {"id": 2, "child": 3},
            {"id": 3, "child": None},
        ]


@pytest.mark.asyncio
async def test_native_aggregate_aliases_and_mixed_group_predicates(acceptance):
    async with acceptance() as session:
        query = AcceptanceParent.objects.values(session, "id", "name").annotate(
            total=Count("children")
        )
        rows = await query.filter(Q(total=0) | Q(name="one")).order_by("id")
        assert rows == [
            {"id": 1, "name": "one", "total": 2},
            {"id": 3, "name": "three", "total": 0},
        ]
        assert await query.filter(total__gte=1).count() == 2


@pytest.mark.asyncio
async def test_native_cte_and_recursive_cte(acceptance):
    async with acceptance() as session:
        totals = (
            AcceptanceParent.objects.annotate(session, doubled=F("score") * 2)
            .values("doubled")
            .cte("audit_totals")
        )
        assert (
            await session.scalars(select(totals.c.doubled).order_by(totals.c.doubled))
        ).all() == [20, 40, 60]
        recursive = select(literal(1).label("number")).cte(
            "audit_numbers", recursive=True
        )
        recursive = recursive.union_all(
            select((recursive.c.number + 1).label("number")).where(
                recursive.c.number < 3
            )
        )
        assert (
            await session.scalars(
                select(recursive.c.number).order_by(recursive.c.number)
            )
        ).all() == [1, 2, 3]


@pytest.mark.asyncio
async def test_native_set_result_filtering_and_pagination(acceptance):
    async with acceptance() as session:
        left = AcceptanceParent.objects.filter(session, id__lte=2).values_list(
            "id", flat=True
        )
        right = AcceptanceParent.objects.filter(session, id__gte=2).values_list(
            "id", flat=True
        )
        assert await left.union(right).filter(id__gt=1).order_by("id").limit(1) == [2]
        assert await left.intersection(right).order_by("id") == [2]
        assert await left.difference(right).order_by("id") == [1]
        assert await left.union(right, all=True).count() == 4


@pytest.mark.asyncio
async def test_native_nested_json_keyset_and_atomic_update(acceptance):
    async with acceptance() as session:
        rows = await AcceptanceParent.objects.filter(
            session, payload__user__age__gte=30, payload__active=True
        ).order_by("id")
        assert [row.id for row in rows] == [3]
        rows = (
            await AcceptanceParent.objects.order_by(session, "score", "id")
            .seek_after(10, 1)
            .limit(2)
        )
        assert [row.id for row in rows] == [2, 3]
        await AcceptanceParent.objects.filter(session, id=1).update(
            score=F("score") + 2
        )
        assert (await AcceptanceParent.objects.get(session, id=1)).score == 12
        await session.rollback()
    async with acceptance() as verifier:
        assert (await AcceptanceParent.objects.get(verifier, id=1)).score == 10


@pytest.mark.asyncio
async def test_native_savepoint_integrity_failure_preserves_outer_transaction(
    acceptance,
):
    async with acceptance() as session:
        async with session.begin():
            await AcceptanceParent.objects.filter(session, id=1).update(score=11)
            with pytest.raises(DatabaseError):
                async with session.begin_nested():
                    await AcceptanceParent.objects.create(
                        session, id=4, name="one", score=40, payload={}
                    )
            assert await AcceptanceParent.objects.count(session) == 3
    async with acceptance() as verifier:
        assert (await AcceptanceParent.objects.get(verifier, id=1)).score == 11


@pytest.mark.asyncio
async def test_native_stream_cancellation_and_connection_reuse(acceptance):
    async with acceptance() as session:
        ready = asyncio.Event()

        async def consume():
            async with aclosing(
                AcceptanceParent.objects.order_by(session, "id").stream()
            ) as stream:
                async for _ in stream:
                    ready.set()
                    await asyncio.Event().wait()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(ready.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await AcceptanceParent.objects.count(session) == 3
