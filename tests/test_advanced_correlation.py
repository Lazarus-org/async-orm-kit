"""Stage 2 correlated-query execution contracts on actual SQLite SQL.

Synchronous SQLite is wrapped in an async-compatible adapter in the shared
fixture; live async driver testing remains a separate release gate.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.orm import aliased
from test_advanced_expression_correctness import (
    AdvAuthor,
    AdvComment,
    AdvPost,
    db as db,
    query,
    run,
    sql,
)

from async_orm_kit import Exists, OuterRef, Subquery
from async_orm_kit.exceptions import FilterError, QueryStateError
from async_orm_kit.query.aggregations import Count, Sum
from async_orm_kit.query.q import Q
from async_orm_kit.queryset.queryset import QuerySet


def test_nested_outer_ref_reaches_grandparent_in_exists(db):
    ada_post_id = (
        db._session.query(AdvPost.id).filter(AdvPost.title == "match").scalar()
    )
    db._session.add(AdvComment(post_id=ada_post_id, text="Ada"))
    db._session.commit()

    comments = QuerySet(AdvComment, db).filter(
        post_id=OuterRef("id"),
        text=OuterRef(OuterRef("name")),
    )
    posts = (
        QuerySet(AdvPost, db)
        .filter(
            author_id=OuterRef("id"),
        )
        .filter(Exists(comments))
    )
    authors = query(db).annotate(found=Exists(posts)).order_by("id")
    assert [(author.name, bool(author.found)) for author in run(authors)] == [
        ("Ada", True),
        ("Grace", False),
        ("Linus", False),
    ]
    assert "__async_orm_outer_ref__" not in sql(authors)


def test_nested_outer_ref_in_scalar_subquery(db):
    comments = (
        QuerySet(AdvComment, db)
        .filter(
            post_id=OuterRef("id"),
        )
        .order_by("-id")
        .values_list("text", flat=True)[:1]
    )
    posts = (
        QuerySet(AdvPost, db)
        .filter(
            author_id=OuterRef("id"),
        )
        .order_by("id")
        .annotate(newest_comment=Subquery(comments))
        .values_list("newest_comment", flat=True)[:1]
    )
    parents = query(db).annotate(text=Subquery(posts)).order_by("id")
    assert [(a.name, a.text) for a in run(parents)] == [
        ("Ada", "x"),
        ("Grace", None),
        ("Linus", None),
    ]


def test_aggregate_scalar_subquery_keeps_original_grouping_columns(db):
    per_author = (
        QuerySet(AdvPost, db)
        .filter(author_id=OuterRef("id"))
        .values("author_id")
        .annotate(total=Count("comments"))
        .values_list("total", flat=True)[:1]
    )
    q = query(db).annotate(total=Subquery(per_author)).order_by("id")
    assert [(a.name, a.total) for a in run(q)] == [
        ("Ada", 2),
        ("Grace", 0),
        ("Linus", None),
    ]
    compiled = sql(q)
    assert "GROUP BY" in compiled
    assert "GROUP BY count(" not in compiled.lower()


def test_aggregate_q_or_is_valid_having_clause(db):
    q = (
        query(db)
        .annotate(total=Sum("score"))
        .filter(Q(total__gte=2) | Q(total__isnull=True))
        .order_by("id")
    )
    assert [a.name for a in run(q)] == ["Ada", "Linus"]
    compiled = sql(q)
    assert "HAVING" in compiled
    assert "WHERE" not in compiled


def test_aggregate_q_and_splits_where_from_having(db):
    q = (
        query(db)
        .annotate(total=Count("posts"))
        .filter(Q(name__in=["Ada", "Linus"]) & (Q(total__gt=1) | Q(total__lt=0)))
    )
    assert [a.name for a in run(q)] == ["Ada"]
    compiled = sql(q)
    assert "WHERE" in compiled and "HAVING" in compiled


def test_explicit_having_accepts_aggregate_only_q(db):
    q = (
        query(db)
        .annotate(total=Count("posts"))
        .having(Q(total__gte=2) | Q(total__lte=0))
        .order_by("id")
    )
    assert [a.name for a in run(q)] == ["Ada", "Linus"]
    with pytest.raises(FilterError, match="aggregate annotations"):
        query(db).annotate(total=Count("posts")).having(Q(name="Ada"))


def test_or_combining_where_and_having_uses_grouped_predicate(db):
    grouped = query(db).annotate(total=Count("posts"))
    matched = grouped.filter(Q(total__gt=1) | Q(name="Grace")).order_by("id")
    assert [row.name for row in run(matched)] == ["Ada", "Grace"]
    assert "HAVING" in sql(matched)
    negated = grouped.filter(~(Q(total__gt=1) | Q(name="Grace"))).order_by("id")
    assert [row.name for row in run(negated)] == ["Linus"]


def test_self_correlated_exists_with_explicit_sqlalchemy_alias(db):
    inner = aliased(AdvAuthor)
    greater = QuerySet(inner, db).filter(
        score__gt=OuterRef("score"),
    )
    q = query(db).filter(Exists(greater)).order_by("id")
    assert [a.name for a in run(q)] == ["Grace"]
    assert "__async_orm_outer_ref__" not in sql(q)


def test_nested_subqueries_compile_for_all_dialects(db):
    comments = QuerySet(AdvComment, db).filter(
        post_id=OuterRef("id"),
        text=OuterRef(OuterRef("name")),
    )
    posts = (
        QuerySet(AdvPost, db)
        .filter(
            author_id=OuterRef("id"),
        )
        .filter(Exists(comments))
    )
    q = query(db).annotate(found=Exists(posts))
    stmt = q._compiler.compile(q._state)
    for dialect in (sqlite.dialect(), postgresql.dialect(), mysql.dialect()):
        compiled = str(stmt.compile(dialect=dialect))
        assert "__async_orm_outer_ref__" not in compiled
        assert "EXISTS" in compiled.upper()


def test_nested_outerref_escaping_top_level_is_rejected(db):
    with pytest.raises(QueryStateError, match="inside Subquery"):
        sql(QuerySet(AdvComment, db).filter(post_id=OuterRef(OuterRef("id"))))


def test_unaliased_self_correlation_is_rejected_not_silently_miscompiled(db):
    unsafe = QuerySet(AdvAuthor, db).filter(score__gt=OuterRef("score"))
    with pytest.raises(QueryStateError, match="aliased"):
        query(db).filter(Exists(unsafe))

    uncorrelated = QuerySet(AdvAuthor, db).filter(name="Ada")
    assert [
        a.name for a in run(query(db).filter(Exists(uncorrelated)).order_by("id"))
    ] == ["Ada", "Grace", "Linus"]


def test_scalar_nested_aggregate_does_not_group_the_outer_queryset(db):
    by_author = (
        QuerySet(AdvPost, db)
        .filter(author_id=OuterRef("id"))
        .values("author_id")
        .annotate(total=Count("id"))
        .values_list("total", flat=True)[:1]
    )
    q = query(db).annotate(post_count=Subquery(by_author)).order_by("id")
    assert q._state.has_aggregates is False
    assert "GROUP BY advanced_expr_authors" not in sql(q)
    assert [(a.name, a.post_count) for a in run(q)] == [
        ("Ada", 2),
        ("Grace", 1),
        ("Linus", None),
    ]


def test_aggregate_expression_rhs_routes_to_having(db):
    from async_orm_kit.query.expressions import F

    q = query(db).annotate(total=Count("posts")).filter(score__gt=F("total"))
    assert "HAVING" in sql(q)
    assert [a.name for a in run(q)] == ["Linus"]
    via_q = (
        query(db)
        .annotate(total=Count("posts"))
        .filter(Q(name="Linus") & Q(score__gt=F("total")))
    )
    assert "WHERE" in sql(via_q) and "HAVING" in sql(via_q)
    assert [a.name for a in run(via_q)] == ["Linus"]

    with pytest.raises(FilterError, match="Aggregate predicates require"):
        query(db).filter(score__gt=Count("posts"))


def test_deep_outerref_depth_is_checked_at_final_query_boundary(db):
    inner = QuerySet(AdvComment, db).filter(text=OuterRef(OuterRef(OuterRef("name"))))
    middle = QuerySet(AdvPost, db).filter(Exists(inner))
    with pytest.raises(QueryStateError, match="inside Subquery"):
        sql(query(db).annotate(remaining=Exists(middle)))


def test_scalar_self_correlation_with_alias_returns_correct_row(db):
    inner = aliased(AdvAuthor)
    following = (
        QuerySet(inner, db)
        .filter(score__gt=OuterRef("score"))
        .order_by("score")
        .values_list("name", flat=True)[:1]
    )
    authors = query(db).annotate(following=Subquery(following)).order_by("id")
    assert [(a.name, a.following) for a in run(authors)] == [
        ("Ada", None),
        ("Grace", "Linus"),
        ("Linus", None),
    ]


def test_correlated_filtered_aggregate_binds_outer_field_without_duplicate_joins(db):
    ada_post = db._session.query(AdvPost).filter(AdvPost.title == "match").one()
    db._session.add(AdvComment(post_id=ada_post.id, text="Ada"))
    db._session.commit()

    conditional = (
        QuerySet(AdvPost, db)
        .filter(author_id=OuterRef("id"))
        .values("author_id")
        .annotate(matched=Count("comments", filter=Q(comments__text=OuterRef("name"))))
        .values_list("matched", flat=True)[:1]
    )
    authors = query(db).annotate(matched=Subquery(conditional)).order_by("id")
    assert [(a.name, a.matched) for a in run(authors)] == [
        ("Ada", 1),
        ("Grace", 0),
        ("Linus", None),
    ]
    compiled = sql(authors)
    assert "CASE WHEN" in compiled
    assert compiled.count("advanced_expr_comments AS") == 1


def test_exists_with_correlated_aggregate_having_predicate(db):
    candidates = (
        QuerySet(AdvPost, db)
        .filter(author_id=OuterRef("id"))
        .annotate(comment_count=Count("comments"))
        .filter(Q(comment_count__gte=1) | Q(comment_count__lt=0))
    )
    authors = query(db).filter(Exists(candidates)).order_by("id")
    assert [a.name for a in run(authors)] == ["Ada"]
    compiled = sql(authors)
    assert "HAVING" in compiled and "EXISTS" in compiled


def test_negated_aggregate_only_q_is_evaluated_in_having(db):
    authors = (
        query(db).annotate(post_count=Count("posts")).filter(~Q(post_count__lte=1))
    )
    assert [a.name for a in run(authors)] == ["Ada"]
    assert "HAVING" in sql(authors)


def test_outerref_nested_constructor_validates_references():
    first = OuterRef("id")
    second = OuterRef(first)
    third = OuterRef(second)
    assert (first.name, first.depth) == ("id", 1)
    assert (second.name, second.depth) == ("id", 2)
    assert (third.name, third.depth) == ("id", 3)
    with pytest.raises(ValueError, match="non-empty"):
        OuterRef("posts____comments")
    with pytest.raises(ValueError, match="non-empty"):
        OuterRef(123)


def test_zero_count_having_keeps_empty_parent_rows(db):
    q = query(db).annotate(total=Count("posts")).filter(total__lte=0).order_by("id")
    assert [person.name for person in run(q)] == ["Linus"]
    assert "LEFT OUTER JOIN" in sql(q)
    assert [
        person.name
        for person in run(query(db).annotate(total=Count("posts")).having(total=0))
    ] == ["Linus"]


def test_self_correlated_aggregate_with_aliased_model(db):
    inner = aliased(AdvAuthor)
    candidates = (
        QuerySet(inner, db)
        .filter(id=OuterRef("id"))
        .annotate(total=Count("posts"))
        .filter(total__gt=0)
    )
    q = query(db).filter(Exists(candidates)).order_by("id")
    assert [person.name for person in run(q)] == ["Ada", "Grace"]
    compiled = sql(q)
    assert "HAVING" in compiled
    assert "GROUP BY advanced_expr_authors_1.id" in compiled
