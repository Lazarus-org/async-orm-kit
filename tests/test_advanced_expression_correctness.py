"""Executable correctness contracts for the advanced ORM expression layer.

Synchronous SQLAlchemy sessions are wrapped in an async-shaped test adapter;
these tests prove real SQLite SQL semantics but do not replace AsyncSession tests.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import DateTime, ForeignKey, Integer, String, create_engine, func
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from async_orm_kit import Model
from async_orm_kit.exceptions import FilterError, QueryStateError
from async_orm_kit.query.aggregations import Count, Sum
from async_orm_kit.query.expressions import Case, F, Value, When
from async_orm_kit.query.q import Q
from async_orm_kit.queryset.queryset import QuerySet


class AdvAuthor(Model):
    __tablename__ = "advanced_expr_authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posts: Mapped[list[AdvPost]] = relationship(back_populates="author")


class AdvPost(Model):
    __tablename__ = "advanced_expr_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("advanced_expr_authors.id"))
    title: Mapped[str] = mapped_column(String(40))
    author: Mapped[AdvAuthor] = relationship(back_populates="posts")
    comments: Mapped[list[AdvComment]] = relationship(back_populates="post")


class AdvComment(Model):
    __tablename__ = "advanced_expr_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("advanced_expr_posts.id"))
    text: Mapped[str] = mapped_column(String(40))
    post: Mapped[AdvPost] = relationship(back_populates="comments")


class AdvEvent(Model):
    __tablename__ = "advanced_expr_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[object] = mapped_column(DateTime)


class AsyncShapedSQLite:
    def __init__(self, session):
        self._session = session
        self.bind = session.bind

    def get_bind(self, mapper=None):
        return self._session.get_bind(mapper=mapper)

    async def execute(self, statement, params=None):
        return self._session.execute(statement, params or {})


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Model.metadata.create_all(
        engine,
        tables=[
            AdvAuthor.__table__,
            AdvPost.__table__,
            AdvComment.__table__,
            AdvEvent.__table__,
        ],
    )
    with Session(engine) as session:
        from datetime import datetime

        session.add(AdvEvent(occurred_at=datetime(2024, 3, 5, 12, 34, 56)))
        session.add_all(
            [
                AdvAuthor(
                    name="Ada",
                    score=None,
                    posts=[
                        AdvPost(title="match", comments=[AdvComment(text="x")]),
                        AdvPost(title="other", comments=[AdvComment(text="y")]),
                    ],
                ),
                AdvAuthor(name="Grace", score=1, posts=[AdvPost(title="other")]),
                AdvAuthor(name="Linus", score=2),
            ]
        )
        session.commit()
        yield AsyncShapedSQLite(session)
    engine.dispose()


def query(session):
    return QuerySet(AdvAuthor, session)


def run(queryset):
    return asyncio.run(queryset._fetch())


def sql(queryset):
    return str(
        queryset._compiler.compile(queryset._state).compile(dialect=sqlite.dialect())
    )


def test_relationship_aggregate_uses_related_pk_and_preserves_empty_parent(db):
    rows = run(query(db).annotate(total=Count("posts")).order_by("id"))
    assert [(row.name, row.total) for row in rows] == [
        ("Ada", 2),
        ("Grace", 1),
        ("Linus", 0),
    ]
    assert "count(advanced_expr_posts_1.id)" in sql(
        query(db).annotate(total=Count("posts"))
    )


def test_nested_relationship_count_does_not_produce_cartesian_product(db):
    rows = run(query(db).annotate(total=Count("posts__comments__id")).order_by("id"))
    assert [(row.name, row.total) for row in rows] == [
        ("Ada", 2),
        ("Grace", 0),
        ("Linus", 0),
    ]


def test_count_f_field_respects_nulls(db):
    rows = run(query(db).annotate(total=Count(F("score"))).order_by("id"))
    assert [row.total for row in rows] == [0, 1, 1]
    assert "count(advanced_expr_authors.score)" in sql(
        query(db).annotate(total=Count(F("score")))
    )
    assert "count(*)" in sql(query(db).annotate(total=Count()))


def test_excluding_a_child_excludes_entire_parent(db):
    rows = run(query(db).exclude(posts__title="match").order_by("id"))
    assert [row.name for row in rows] == ["Grace", "Linus"]
    assert "EXISTS" in sql(query(db).exclude(posts__title="match"))
    assert "JOIN" not in sql(query(db).exclude(posts__title="match"))


def test_excluding_nested_child_uses_correlated_exists(db):
    rows = run(query(db).exclude(posts__comments__text="x").order_by("id"))
    assert [row.name for row in rows] == ["Grace", "Linus"]


def test_negated_q_and_exclude_agree_on_nullable_values(db):
    via_q = run(query(db).filter(~Q(score=1)).order_by("id"))
    via_exclude = run(query(db).exclude(score=1).order_by("id"))
    assert [row.name for row in via_q] == ["Ada", "Linus"]
    assert [row.name for row in via_exclude] == ["Ada", "Linus"]
    assert [
        row.name
        for row in run(query(db).filter(~Q(posts__title="match")).order_by("id"))
    ] == ["Grace", "Linus"]


def test_aggregate_annotation_filter_emits_having_and_executes(db):
    result = query(db).annotate(total=Sum("score")).filter(total__gt=1)
    assert "HAVING" in sql(result)
    assert [row.name for row in run(result)] == ["Linus"]


def test_aggregate_annotation_filter_separates_where_and_having(db):
    result = (
        query(db)
        .annotate(total=Sum("score"))
        .filter(name__in=["Ada", "Linus"], total__gt=1)
    )
    compiled = sql(result)
    assert "WHERE" in compiled and "HAVING" in compiled
    assert [row.name for row in run(result)] == ["Linus"]


def test_aggregate_filters_in_q_mixed_or_is_grouped(db):
    q = (
        query(db)
        .annotate(total=Sum("score"))
        .filter(Q(total__gt=1) | Q(name="Ada"))
        .order_by("id")
    )
    assert [row.name for row in run(q)] == ["Ada", "Linus"]
    assert "HAVING" in sql(q)


def test_recursive_aggregate_detection_for_arithmetic_case_and_sqlalchemy(db):
    arith = query(db).annotate(total=Sum("score") + 1)
    sql_arith = sql(arith)
    assert "GROUP BY" in sql_arith
    assert [(row.name, row.total) for row in run(arith.order_by("id"))] == [
        ("Ada", None),
        ("Grace", 2),
        ("Linus", 3),
    ]
    conditional = query(db).annotate(
        total=Case(When(name="Grace", then=Sum("score")), default=Value(0))
    )
    assert "GROUP BY" in sql(conditional)
    sqlalchemy_aggregate = query(db).annotate(total=func.sum(AdvAuthor.score))
    assert "GROUP BY" in sql(sqlalchemy_aggregate)


def test_filtered_aggregates_and_result_cast_defaults(db):
    from sqlalchemy import Integer as IntegerType

    result = query(db).annotate(
        selected=Count("posts", filter=Q(posts__title="match")),
        total=Sum("score", default=0, output_field=IntegerType()),
    )
    rows = run(result.order_by("id"))
    assert [(row.name, row.selected, row.total) for row in rows] == [
        ("Ada", 1, 0),
        ("Grace", 0, 1),
        ("Linus", 0, 2),
    ]
    with pytest.raises(TypeError, match="Unsupported aggregate options"):
        Sum("score", typo=True)
    with pytest.raises(TypeError, match="Q expression"):
        Sum("score", filter={"score": 1})


def test_ordering_aggregate_expression(db):
    rows = run(
        query(db).annotate(total=Count("posts")).order_by(Count("posts").desc(), "name")
    )
    assert [row.name for row in rows] == ["Ada", "Grace", "Linus"]


def test_annotated_get_materializes_result(db):
    obj = asyncio.run(query(db).annotate(answer=Value(77)).get(name="Ada"))
    assert obj.answer == 77


def test_values_get_uses_projection_shape(db):
    obj = asyncio.run(query(db).values("name").get(name="Ada"))
    assert obj == {"name": "Ada"}
    name = asyncio.run(query(db).values_list("name", flat=True).get(name="Ada"))
    assert name == "Ada"


def test_custom_queryset_retains_subclass_and_extension_state(db):
    class CustomQuerySet(QuerySet):
        def __init__(self, model, session):
            super().__init__(model, session)
            self.custom_state = {"marker": 1}

        def special(self):
            return self.filter(score__gt=0)

    base = CustomQuerySet(AdvAuthor, db)
    clone = base.filter(name="Ada").order_by("id")
    assert isinstance(clone, CustomQuerySet)
    assert isinstance(clone.special(), CustomQuerySet)
    assert clone.custom_state == {"marker": 1}


def test_queryset_in_subquery_projects_single_primary_key(db):
    authors = query(db).filter(id__in=query(db).filter(name="Ada"))
    assert [row.name for row in run(authors)] == ["Ada"]
    one_column = query(db).filter(id__in=query(db).values_list("id", flat=True))
    assert len(run(one_column)) == 3


def test_queryset_in_subquery_rejects_multiple_columns(db):
    with pytest.raises(QueryStateError, match="exactly one column"):
        query(db).filter(id__in=query(db).values("id", "score"))


def test_terminal_aggregate_returns_named_summary(db):
    result = asyncio.run(
        query(db).aggregate(
            records=Count(), non_null=Count("score"), total=Sum("score")
        )
    )
    assert result == {"records": 3, "non_null": 2, "total": 3}
    filtered = asyncio.run(query(db).filter(score__gte=1).aggregate(total=Sum("score")))
    assert filtered == {"total": 3}


def test_terminal_aggregate_rejects_ambiguous_grouping(db):
    with pytest.raises(QueryStateError, match="ungrouped"):
        asyncio.run(query(db).values("name").aggregate(total=Sum("score")))


def test_when_with_sql_expressions_compares_as_boolean_and_hashes_collections():
    left = When(score__in=[1, 2], then=F("score"))
    right = When(score__in=[1, 2], then=F("name"))
    assert (left == right) is False
    assert isinstance(left == right, bool)
    assert isinstance(hash(left), int)


def test_expression_based_update_is_atomic_and_resolves_case_and_value(db):
    updated = asyncio.run(query(db).filter(name="Grace").update(score=F("score") + 3))
    assert updated == 1
    assert [(item.name, item.score) for item in run(query(db).order_by("id"))] == [
        ("Ada", None),
        ("Grace", 4),
        ("Linus", 2),
    ]

    asyncio.run(
        query(db)
        .filter(name="Grace")
        .update(score=Case(When(score__gte=4, then=F("score") + 2), default=Value(0)))
    )
    assert run(query(db).filter(name="Grace"))[0].score == 6
    asyncio.run(query(db).filter(name="Grace").update(score=Value(8)))
    assert run(query(db).filter(name="Grace"))[0].score == 8


def test_portable_function_expressions_execute_on_sqlite(db):
    from sqlalchemy import String as SAString

    from async_orm_kit.query.expressions import (
        Cast,
        Coalesce,
        Concat,
        Extract,
        Length,
        Lower,
        NullIf,
        Round,
        Trim,
        Upper,
    )

    rows = run(
        query(db)
        .annotate(
            lowercase=Lower(F("name")),
            uppercase=Upper(F("name")),
            label=Concat(F("name"), Value("-ok")),
            size=Length(F("name")),
            score_or_zero=Coalesce(F("score"), Value(0)),
            as_text=Cast(F("score"), SAString()),
            no_score=NullIf(F("score"), Value(1)),
            trimmed=Trim(Concat(Value("  "), F("name"))),
            rounded=Round(F("score"), 0),
        )
        .order_by("id")
    )
    assert rows[0].lowercase == "ada"
    assert rows[0].uppercase == "ADA"
    assert rows[0].label == "Ada-ok"
    assert rows[0].size == 3
    assert rows[0].score_or_zero == 0
    assert rows[1].as_text == "1"
    assert rows[1].no_score is None
    assert rows[0].trimmed == "Ada"
    assert rows[1].rounded == 1.0

    from datetime import date

    year = run(
        query(db).annotate(year=Extract("year", Value(date(2024, 3, 5)))).order_by("id")
    )
    assert year[0].year == 2024

    with pytest.raises(ValueError, match="Extract part"):
        Extract("year); DROP TABLE authors", F("score"))
    with pytest.raises(TypeError, match="TypeEngine"):
        Cast(F("score"), "INTEGER")
    with pytest.raises(ValueError, match="at least two"):
        Coalesce(F("score"))


def test_window_ranking_and_running_total_without_group_by(db):
    from async_orm_kit.query.expressions import DenseRank, Lag, RowNumber, Window

    query_window = (
        query(db)
        .annotate(
            position=Window(RowNumber(), order_by=[F("id").asc()]),
            previous_name=Window(Lag(F("name")), order_by=[F("id").asc()]),
            running=Window(Sum("score"), order_by=[F("id").asc()]),
        )
        .order_by("id")
    )
    text = sql(query_window)
    assert " OVER (" in text
    assert " GROUP BY " not in text
    rows = run(query_window)
    assert [row.position for row in rows] == [1, 2, 3]
    assert [row.previous_name for row in rows] == [None, "Ada", "Grace"]
    assert [row.running for row in rows] == [None, 1, 3]
    with pytest.raises(ValueError, match="require order_by"):
        Window(DenseRank())
    with pytest.raises(FilterError, match="Window annotations cannot be filtered"):
        query_window.filter(position__gte=2)


def test_aggregate_filter_sql_compiles_for_postgresql_and_mysql(db):
    from sqlalchemy.dialects import mysql, postgresql

    from async_orm_kit.query.aggregations import Count

    q = query(db).annotate(total=Count("posts", filter=Q(posts__title="match")))
    stmt = q._compiler.compile(q._state)
    for dialect in (postgresql.dialect(), mysql.dialect()):
        rendered = str(stmt.compile(dialect=dialect))
        assert "count(" in rendered.lower()
        assert "CASE WHEN" in rendered.upper()


def test_sql_function_and_window_dialect_compilation(db):
    from sqlalchemy.dialects import mysql, postgresql

    from async_orm_kit.query.expressions import Concat, Lower, RowNumber, Window

    q = query(db).annotate(
        label=Concat(Lower(F("name")), Value("-ok")),
        position=Window(RowNumber(), order_by=[F("id").asc()]),
    )
    stmt = q._compiler.compile(q._state)
    for dialect in (sqlite.dialect(), postgresql.dialect(), mysql.dialect()):
        rendered = str(stmt.compile(dialect=dialect))
        assert (
            "row_number() OVER" in rendered or "row_number() OVER" in rendered.lower()
        )
        assert "lower(" in rendered.lower()


def test_bitwise_floor_division_and_explicit_value_comparison(db):
    from async_orm_kit.query.expressions import F

    row = run(
        query(db)
        .filter(name="Grace")
        .annotate(
            floor=F("score") // 2,
            bits=F("score") & 3,
            either=F("score") | 4,
            inverse=~F("score"),
            equal=Value(1).eq(F("score")),
        )
    )[0]
    assert row.floor == 0
    assert row.bits == 1
    assert row.either == 5
    assert row.inverse == -2
    assert row.equal in (True, 1)


def test_date_part_lookups_are_parameterized_and_restrict_types(db):
    q = QuerySet(AdvEvent, db).filter(occurred_at__year=2024, occurred_at__month=3)
    rows = run(q)
    assert len(rows) == 1
    assert "EXTRACT" in sql(q).upper() or "STRFTIME" in sql(q).upper()

    with pytest.raises((TypeError, FilterError), match="date/time column"):
        query(db).filter(score__year=2024)
    with pytest.raises((TypeError, FilterError), match="integer value"):
        QuerySet(AdvEvent, db).filter(occurred_at__year="2024")


def test_row_locking_compiles_postgres_and_rejects_sqlite(db):
    from sqlalchemy.dialects import mysql, postgresql

    q = query(db).filter(name="Ada").select_for_update(nowait=True)
    statement = q._compiler.compile(q._state)
    assert "FOR UPDATE NOWAIT" in str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE NOWAIT" in str(statement.compile(dialect=mysql.dialect()))
    with pytest.raises(NotImplementedError, match="sqlite"):
        run(q)
    with pytest.raises(ValueError, match="mutually exclusive"):
        query(db).select_for_update(nowait=True, skip_locked=True)
    with pytest.raises(QueryStateError, match="Row locks"):
        asyncio.run(q.count())
    with pytest.raises(QueryStateError, match="Row locks"):
        asyncio.run(q.exists())
    with pytest.raises(QueryStateError, match="select_for_update"):
        asyncio.run(q.aggregate(total=Count()))


def test_nested_bare_relationship_aggregate(db):
    rows = run(query(db).annotate(total=Count("posts__comments")).order_by("id"))
    assert [row.total for row in rows] == [2, 0, 0]


def test_update_rejects_joined_and_grouped_expression_values(db):
    with pytest.raises(NotImplementedError, match="traverse relationships"):
        asyncio.run(query(db).update(score=F("posts__id")))
    with pytest.raises(ValueError, match="aggregate or window"):
        asyncio.run(query(db).update(score=Sum("score")))


def test_terminal_aggregate_rejects_distinct_query_without_silent_semantic_change(db):
    with pytest.raises(QueryStateError, match="ungrouped"):
        asyncio.run(query(db).distinct().aggregate(total=Count()))


def test_when_structural_equality_and_snapshot_input_collections():
    selected = [1, 2]
    first = When(score__in=selected, then=F("score"))
    selected.append(3)
    assert first.condition["score__in"] == [1, 2]
    second = When(score__in=[1, 2], then=F("score"))
    assert first == second
    assert hash(first) == hash(second)
    assert first != When(score__in=[1, 2], then=F("name"))


def test_correlated_subquery_returns_latest_per_parent(db):
    from async_orm_kit import OuterRef, Subquery

    latest = (
        QuerySet(AdvPost, db)
        .filter(author_id=OuterRef("id"))
        .order_by("-id")
        .values_list("title", flat=True)[:1]
    )
    q = query(db).annotate(latest_title=Subquery(latest)).order_by("id")
    assert [(item.name, item.latest_title) for item in run(q)] == [
        ("Ada", "other"),
        ("Grace", "other"),
        ("Linus", None),
    ]
    assert "SELECT" in sql(q)
    assert "__async_orm_outer_ref__" not in sql(q)


def test_correlated_exists_works_in_annotation_and_filter(db):
    from async_orm_kit import Exists, OuterRef

    matching_posts = QuerySet(AdvPost, db).filter(
        author_id=OuterRef("id"), title="match"
    )
    q = query(db).annotate(has_match=Exists(matching_posts)).order_by("id")
    assert [(item.name, bool(item.has_match)) for item in run(q)] == [
        ("Ada", True),
        ("Grace", False),
        ("Linus", False),
    ]
    assert [item.name for item in run(query(db).filter(Exists(matching_posts)))] == [
        "Ada"
    ]
    assert [
        item.name
        for item in run(query(db).filter(~Exists(matching_posts)).order_by("id"))
    ] == ["Grace", "Linus"]


def test_outerref_cannot_escape_correlated_query_and_subquery_shapes_validated(db):
    from async_orm_kit import OuterRef, Subquery

    with pytest.raises(QueryStateError, match="inside Subquery"):
        sql(QuerySet(AdvPost, db).filter(author_id=OuterRef("id")))
    with pytest.raises(QueryStateError, match="single selected column"):
        query(db).annotate(
            invalid=Subquery(QuerySet(AdvPost, db).values("id", "title"))
        )
    with pytest.raises(TypeError, match="requires a QuerySet"):
        Subquery("SELECT 1")


def test_correlated_subquery_and_exists_compile_for_postgres_mysql_sqlite(db):
    from sqlalchemy.dialects import mysql, postgresql

    from async_orm_kit import Exists, OuterRef, Subquery

    inner = QuerySet(AdvPost, db).filter(author_id=OuterRef("id"))
    scalar = inner.order_by("-id").values_list("title", flat=True)[:1]
    outer = query(db).annotate(newest=Subquery(scalar), has_posts=Exists(inner))
    stmt = outer._compiler.compile(outer._state)
    for dialect in (sqlite.dialect(), postgresql.dialect(), mysql.dialect()):
        compiled = str(stmt.compile(dialect=dialect))
        assert "EXISTS" in compiled.upper()
        assert "advanced_expr_posts" in compiled
        assert "__async_orm_outer_ref__" not in compiled
