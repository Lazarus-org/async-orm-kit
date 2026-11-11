"""Live AsyncSession regression contracts for deeply correlated query execution.

Requires the optional aiosqlite driver; module is skipped locally when absent.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

pytest.importorskip("aiosqlite")

from sqlalchemy import ForeignKey, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, aliased, mapped_column, relationship

from async_orm_kit import Exists, Model, OuterRef, Subquery
from async_orm_kit.query.aggregations import Count
from async_orm_kit.query.q import Q
from async_orm_kit.queryset.queryset import QuerySet


class CorPerson(Model):
    __tablename__ = "advanced_cor_people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    rank: Mapped[int] = mapped_column()
    articles: Mapped[list[CorArticle]] = relationship(back_populates="person")


class CorArticle(Model):
    __tablename__ = "advanced_cor_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("advanced_cor_people.id"))
    title: Mapped[str] = mapped_column(String(80))

    person: Mapped[CorPerson] = relationship(back_populates="articles")
    notes: Mapped[list[CorNote]] = relationship(back_populates="article")


class CorNote(Model):
    __tablename__ = "advanced_cor_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("advanced_cor_articles.id"))
    body: Mapped[str] = mapped_column(String(80))

    article: Mapped[CorArticle] = relationship(back_populates="notes")


@pytest_asyncio.fixture
async def cor_sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'correlations.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Model.metadata.create_all(
                conn,
                tables=[CorPerson.__table__, CorArticle.__table__, CorNote.__table__],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                CorPerson(
                    name="Ada",
                    rank=0,
                    articles=[
                        CorArticle(
                            title="match",
                            notes=[CorNote(body="Ada"), CorNote(body="different")],
                        ),
                        CorArticle(title="other"),
                    ],
                ),
                CorPerson(name="Grace", rank=1, articles=[CorArticle(title="COBOL")]),
                CorPerson(name="Linus", rank=2),
            ]
        )
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_nested_outerref_exists_against_real_async_session(cor_sessions):
    async with cor_sessions() as session:
        notes = QuerySet(CorNote, session).filter(
            article_id=OuterRef("id"),
            body=OuterRef(OuterRef("name")),
        )
        articles = (
            QuerySet(CorArticle, session)
            .filter(person_id=OuterRef("id"))
            .filter(Exists(notes))
        )
        result = (
            await QuerySet(CorPerson, session).filter(Exists(articles)).order_by("id")
        )
        assert [person.name for person in result] == ["Ada"]


@pytest.mark.asyncio
async def test_aggregate_scalar_subquery_and_having_q_async(cor_sessions):
    async with cor_sessions() as session:
        totals = (
            QuerySet(CorArticle, session)
            .filter(person_id=OuterRef("id"))
            .values("person_id")
            .annotate(note_count=Count("notes"))
            .values_list("note_count", flat=True)[:1]
        )
        people = (
            await QuerySet(CorPerson, session)
            .annotate(total=Subquery(totals))
            .order_by("id")
        )
        assert [(person.name, person.total) for person in people] == [
            ("Ada", 2),
            ("Grace", 0),
            ("Linus", None),
        ]

        matching = (
            await QuerySet(CorPerson, session)
            .annotate(article_count=Count("articles"))
            .filter(Q(name="Ada") & Q(article_count__gte=2))
        )
        assert [person.name for person in matching] == ["Ada"]


@pytest.mark.asyncio
async def test_explicit_aliased_self_correlation_async(cor_sessions):
    async with cor_sessions() as session:
        inner = aliased(CorPerson)
        more_senior = QuerySet(inner, session).filter(rank__gt=OuterRef("rank"))
        result = (
            await QuerySet(CorPerson, session)
            .filter(Exists(more_senior))
            .order_by("id")
        )
        assert [person.name for person in result] == ["Ada", "Grace"]
