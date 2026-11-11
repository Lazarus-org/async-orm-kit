"""Execution-level Stage 7 contracts for grouped predicates and advanced lookup APIs."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import Integer, String, create_engine
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.exc import CompileError
from sqlalchemy.orm import Mapped, Session, mapped_column
from test_advanced_expression_correctness import (
    db as _authors_db,
    query as authors_query,
    run as authors_run,
)
from test_advanced_sql import PgSQLItem, SQLItem, db as db, run

from async_orm_kit import Exists, F, Model, OuterRef
from async_orm_kit.exceptions import InvalidLookupError, QueryStateError
from async_orm_kit.query.aggregations import Count
from async_orm_kit.query.q import Q
from async_orm_kit.queryset.queryset import QuerySet


class Stage7CompositeParent(Model):
    __tablename__ = "advanced_stage7_composite_parent"
    tenant: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), primary_key=True)


class Stage7CompositeChild(Model):
    __tablename__ = "advanced_stage7_composite_child"
    tenant: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), primary_key=True)


@pytest.fixture
def composite_db():
    from test_advanced_sql import Adapter

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Stage7CompositeParent.__table__.create(engine)
    Stage7CompositeChild.__table__.create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Stage7CompositeParent(tenant=1, code="a"),
                Stage7CompositeParent(tenant=1, code="b"),
                Stage7CompositeParent(tenant=2, code="a"),
                Stage7CompositeChild(tenant=1, code="a"),
                Stage7CompositeChild(tenant=2, code="a"),
            ]
        )
        session.commit()
        yield Adapter(session)
    engine.dispose()


def test_json_nested_paths_and_types_execute(db):
    query = QuerySet(SQLItem, db)
    assert [
        x.id for x in run(query.filter(payload__user__name="Ada").order_by("id"))
    ] == [1, 3]
    assert [
        x.id for x in run(query.filter(payload__user__age__gte=41).order_by("id"))
    ] == [2, 3]
    assert [x.id for x in run(query.filter(payload__active=True).order_by("id"))] == [
        1,
        3,
    ]
    assert [
        x.id
        for x in run(query.filter(payload__user__name__icontains="gr").order_by("id"))
    ] == [2]
    assert [
        x.id
        for x in run(query.filter(payload__user__name__startswith="A").order_by("id"))
    ] == [1, 3]
    assert [
        x.id for x in run(query.filter(payload__user__age__in=[35, 50]).order_by("id"))
    ] == [1, 3]
    assert [
        x.id for x in run(query.filter(payload__missing__isnull=True).order_by("id"))
    ] == [1, 2, 3]


def test_json_array_index_and_nested_nulls(db):
    db._session.add(
        SQLItem(
            id=4,
            label="delta",
            score=0,
            payload={
                "values": [{"label": "x"}, {"label": "y"}],
                "user": {"name": None},
            },
        )
    )
    db._session.commit()
    query = QuerySet(SQLItem, db)
    assert [x.id for x in run(query.filter(payload__values__0__label="x"))] == [4]
    assert [x.id for x in run(query.filter(payload__values__1__label="y"))] == [4]
    assert [
        x.id for x in run(query.filter(payload__user__name__isnull=True).order_by("id"))
    ] == [4]


def test_json_lookup_sql_is_parameterized_across_dialects(db):
    malicious = "city' OR 1=1 --"
    q = QuerySet(SQLItem, db).filter(**{f"payload__user__{malicious}": "Ada"})
    for dialect in (sqlite.dialect(), mysql.dialect(), postgresql.dialect()):
        statement = q._compiler.compile(q.state)
        compiled = statement.compile(dialect=dialect)
        assert malicious not in str(compiled)
        assert malicious in str(compiled.params)


def test_jsonb_lookup_compiles_only_for_postgres(db):
    q = QuerySet(PgSQLItem, db).filter(payload__contains={"enabled": True})
    assert "@>" in q.sql(dialect=postgresql.dialect())
    with pytest.raises(CompileError):
        q.sql(dialect=sqlite.dialect())
    q2 = QuerySet(PgSQLItem, db).filter(payload__has_key="access")
    assert "?" in q2.sql(dialect=postgresql.dialect())
    q3 = QuerySet(PgSQLItem, db).filter(payload__has_keys=["a", "b"])
    assert "?&" in q3.sql(dialect=postgresql.dialect())
    q4 = QuerySet(PgSQLItem, db).filter(payload__has_any_keys=["a", "b"])
    assert "?|" in q4.sql(dialect=postgresql.dialect())


def test_json_unsafe_root_text_fallback_fails(db):
    with pytest.raises(InvalidLookupError):
        QuerySet(SQLItem, db).filter(payload__icontains="Ada")
    with pytest.raises(InvalidLookupError):
        QuerySet(SQLItem, db).filter(payload__user__name__has_key="unsafe")


def test_compound_filter_after_union_on_values(db):
    query = QuerySet(SQLItem, db)
    left = query.filter(score__lte=20).values("id", "label")
    right = query.filter(score__gte=20).values("id", "label")
    union = left.union(right)
    filtered = union.filter(Q(id__gte=2) & ~Q(label="gamma")).order_by("id")
    assert run(filtered._fetch()) == [{"id": 2, "label": "beta"}]
    assert run(union._fetch()) == [
        {"id": 1, "label": "alpha"},
        {"id": 2, "label": "beta"},
        {"id": 3, "label": "gamma"},
    ]


def test_compound_filtered_model_hydration_and_pagination(db):
    query = QuerySet(SQLItem, db)
    combined = query.filter(score__lte=20).union(query.filter(score__gte=20))
    page = combined.filter(Q(score__gte=20) | Q(id=1)).order_by("id").limit(2)
    assert [(x.id, x.label) for x in run(page._fetch())] == [(1, "alpha"), (2, "beta")]
    assert asyncio.run(page.count()) == 2
    assert asyncio.run(page.exists())


def test_compound_exclude_null_semantics_and_guard(db):
    query = QuerySet(SQLItem, db)
    union = (
        query.filter(score__lt=20)
        .values_list("id", flat=True)
        .union(query.filter(score__gte=20).values_list("id", flat=True))
    )
    assert run(union.exclude(id=2).order_by("id")._fetch()) == [1, 3]
    with pytest.raises(QueryStateError, match="not projected"):
        union.filter(label="alpha")
    with pytest.raises(QueryStateError, match="before order_by"):
        union.order_by("id").filter(id=1)
    with pytest.raises(QueryStateError, match="before order_by"):
        union.limit(2).filter(id=1)


def test_grouped_mixed_or_not_and_zero_count(authors_db):
    query = authors_query(authors_db).annotate(total=Count("posts"))
    match = query.filter(Q(total__gt=1) | Q(name="Grace")).order_by("id")
    assert [x.name for x in authors_run(match)] == ["Ada", "Grace"]
    unmatched = query.filter(~(Q(total__gt=1) | Q(name="Grace"))).order_by("id")
    assert [x.name for x in authors_run(unmatched)] == ["Linus"]
    all_groups = query.filter(Q(total__gte=0) | Q(name="nowhere"))
    assert [x.name for x in authors_run(all_groups.order_by("id"))] == [
        "Ada",
        "Grace",
        "Linus",
    ]


def test_grouped_mixed_or_same_child_and_semantics(authors_db):
    query = authors_query(authors_db).annotate(total=Count("posts"))
    mixed = query.filter(
        Q(total__gt=3) | (Q(posts__title="match") & Q(posts__title="other"))
    )
    assert authors_run(mixed) == []
    matched = query.filter(Q(total__gt=3) | Q(posts__title="match"))
    assert [x.name for x in authors_run(matched)] == ["Ada"]


def test_composite_pk_filter_and_in_lookup(composite_db):
    parents = QuerySet(Stage7CompositeParent, composite_db)
    assert [(x.tenant, x.code) for x in run(parents.filter(pk=(1, "a")))] == [(1, "a")]
    assert [
        (x.tenant, x.code)
        for x in run(
            parents.filter(pk__in=[(1, "a"), (2, "a")]).order_by("tenant", "code")
        )
    ] == [(1, "a"), (2, "a")]


def test_composite_pk_correlation_in_exists(composite_db):
    parents = QuerySet(Stage7CompositeParent, composite_db)
    children = QuerySet(Stage7CompositeChild, composite_db).filter(pk=OuterRef("pk"))
    matched = parents.filter(Exists(children)).order_by("tenant", "code")
    assert [(x.tenant, x.code) for x in run(matched)] == [(1, "a"), (2, "a")]


def test_grouped_exclude_aggregate_and_mixed_q(authors_db):
    grouped = authors_query(authors_db).annotate(total=Count("posts"))
    assert [
        x.name for x in authors_run(grouped.exclude(total__gte=2).order_by("id"))
    ] == [
        "Grace",
        "Linus",
    ]
    assert [
        x.name
        for x in authors_run(
            grouped.exclude(Q(name="Grace") | Q(total__gt=1)).order_by("id")
        )
    ] == ["Linus"]
    assert [
        x.name
        for x in authors_run(
            grouped.exclude(Q(name="Ada") & Q(total__gt=1)).order_by("id")
        )
    ] == ["Grace", "Linus"]


def test_mixed_relationship_predicate_does_not_inflate_aggregate(authors_db):
    query = authors_query(authors_db).annotate(total=Count("posts"))
    matched = query.filter(Q(total__gt=3) | Q(posts__comments__text="x")).order_by("id")
    rows = authors_run(matched)
    assert [(x.name, x.total) for x in rows] == [("Ada", 2)]
    statement = matched.sql(dialect=sqlite.dialect()).upper()
    assert "EXISTS" in statement and "HAVING" in statement


def test_composite_pk_aliased_self_correlation(composite_db):
    from sqlalchemy.orm import aliased

    aliased_parent = aliased(Stage7CompositeParent)
    inner = QuerySet(aliased_parent, composite_db).filter(pk=OuterRef("pk"))
    matched = QuerySet(Stage7CompositeParent, composite_db).filter(Exists(inner))
    assert [(x.tenant, x.code) for x in run(matched.order_by("tenant", "code"))] == [
        (1, "a"),
        (1, "b"),
        (2, "a"),
    ]


def test_compound_filter_after_except_and_intersection(db):
    query = QuerySet(SQLItem, db)
    left = query.filter(id__lte=3).values_list("label", flat=True)
    right = query.filter(id__gte=2).values_list("label", flat=True)
    assert run(left.difference(right).filter(label__startswith="a")._fetch()) == [
        "alpha"
    ]
    assert run(left.intersection(right).filter(label__icontains="g")._fetch()) == [
        "gamma"
    ]


def test_json_comparison_to_f_expression_uses_numeric_extraction(db):
    db._session.add(
        SQLItem(
            id=4,
            label="delta",
            score=999,
            payload={"user": {"age": 5}},
        )
    )
    db._session.commit()
    query = (
        QuerySet(SQLItem, db).filter(payload__user__age__gt=F("score")).order_by("id")
    )
    assert [x.id for x in run(query)] == [1, 2, 3]
    compiled = query.sql(dialect=sqlite.dialect())
    assert "JSON_QUOTE" not in compiled.upper()


# Reuse the registered fixture while keeping the import visible to linters.
authors_db = _authors_db
