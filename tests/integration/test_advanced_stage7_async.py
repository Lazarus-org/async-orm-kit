"""Stage 7 native AsyncSession SQLite contract: JSON, grouped Q and set filters."""

from __future__ import annotations

import pytest
import pytest_asyncio

pytest.importorskip("aiosqlite")

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

from async_orm_kit import Exists, Model, OuterRef
from async_orm_kit.query.aggregations import Count
from async_orm_kit.query.q import Q


class Stage7AsyncAuthor(Model):
    __tablename__ = "stage7_async_authors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    posts: Mapped[list[Stage7AsyncPost]] = relationship(back_populates="author")


class Stage7AsyncPost(Model):
    __tablename__ = "stage7_async_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("stage7_async_authors.id"))
    author: Mapped[Stage7AsyncAuthor] = relationship(back_populates="posts")


class Stage7AsyncComposite(Model):
    __tablename__ = "stage7_async_composite"
    tenant: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)


class Stage7AsyncCompositeCopy(Model):
    __tablename__ = "stage7_async_composite_copy"
    tenant: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage7.db'}")
    tables = [
        Stage7AsyncAuthor.__table__,
        Stage7AsyncPost.__table__,
        Stage7AsyncComposite.__table__,
        Stage7AsyncCompositeCopy.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Model.metadata.create_all(sync, tables=tables)
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    Stage7AsyncAuthor(
                        id=1,
                        name="Ada",
                        payload={"user": {"age": 35}, "tags": ["x", "y"]},
                        posts=[Stage7AsyncPost(), Stage7AsyncPost()],
                    ),
                    Stage7AsyncAuthor(
                        id=2,
                        name="Grace",
                        payload={"user": {"age": 41}, "tags": ["z"]},
                        posts=[Stage7AsyncPost()],
                    ),
                    Stage7AsyncAuthor(
                        id=3,
                        name="Linus",
                        payload={"user": {"age": 50}, "tags": []},
                    ),
                    Stage7AsyncComposite(tenant=1, key="a"),
                    Stage7AsyncComposite(tenant=1, key="b"),
                    Stage7AsyncCompositeCopy(tenant=1, key="a"),
                ]
            )
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_native_async_nested_json_lookup_and_index(factory):
    async with factory() as session:
        rows = await Stage7AsyncAuthor.objects.filter(
            session, payload__user__age__gte=41
        ).order_by("id")
        assert [x.name for x in rows] == ["Grace", "Linus"]
        rows = await Stage7AsyncAuthor.objects.filter(session, payload__tags__1="y")
        assert [x.name for x in rows] == ["Ada"]


@pytest.mark.asyncio
async def test_native_async_compound_result_filter_and_count(factory):
    async with factory() as session:
        base = Stage7AsyncAuthor.objects.all(session)
        combined = base.filter(id__lte=2).union(base.filter(id__gte=2))
        result = await combined.filter(name__icontains="a").order_by("id")
        assert [x.name for x in result] == ["Ada", "Grace"]
        assert await combined.filter(id__gte=2).count() == 2


@pytest.mark.asyncio
async def test_native_async_grouped_mixed_q_or_and_not(factory):
    async with factory() as session:
        grouped = Stage7AsyncAuthor.objects.annotate(session, total=Count("posts"))
        matched = await grouped.filter(Q(total__gt=1) | Q(name="Grace")).order_by("id")
        assert [x.name for x in matched] == ["Ada", "Grace"]
        unmatched = await grouped.filter(~(Q(total__gt=1) | Q(name="Grace"))).order_by(
            "id"
        )
        assert [x.name for x in unmatched] == ["Linus"]


@pytest.mark.asyncio
async def test_native_async_composite_pk_outer_ref(factory):
    async with factory() as session:
        children = Stage7AsyncCompositeCopy.objects.filter(session, pk=OuterRef("pk"))
        parents = Stage7AsyncComposite.objects.filter(
            session, Exists(children)
        ).order_by("tenant", "key")
        matched = await parents
        assert [(x.tenant, x.key) for x in matched] == [(1, "a")]
