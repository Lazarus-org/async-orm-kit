"""SQL-generation gates shared by SQLite, PostgreSQL and MySQL contracts.

These assertions run without optional database drivers. They validate compilation
and parameter binding, not server execution (covered by the live CI jobs).
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import JSON, Integer, String
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import CompileError
from sqlalchemy.orm import Mapped, mapped_column

from async_orm_kit import (
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


class Stage6JSONModel(Model):
    __tablename__ = "stage6_dialect_json_compile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class Stage6PGModel(Model):
    __tablename__ = "stage6_dialect_postgres_compile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)))


@pytest.mark.parametrize(
    ("dialect", "json_token"),
    [
        (sqlite.dialect(), "JSON_EXTRACT"),
        (postgresql.dialect(), "#>>"),
        (mysql.dialect(), "JSON_EXTRACT"),
    ],
)
def test_typed_json_predicates_use_bound_data(dialect, json_token):
    model = Stage6JSONModel
    qs = (
        model.objects.annotate(
            object(),
            age=JSONPath(F("payload"), "user", "age", as_type="integer"),
        )
        .filter(age__gte=834729)
        .values("id", "age")
    )
    compiled = qs._compiler.compile(qs.state).compile(dialect=dialect)
    sql = str(compiled).upper()
    assert json_token in sql
    assert "834729" not in sql
    assert 834729 in compiled.params.values()


def test_postgres_array_and_jsonb_predicates_have_typed_bound_parameters():
    qs = Stage6PGModel.objects.annotate(
        object(),
        contains=JSONContains(F("payload"), {"safe": "secret-value"}),
        key=JSONHasKey(F("payload"), "user' or true --"),
        overlap=ArrayOverlap(F("tags"), ["mysql", "postgres"]),
        members=ArrayContains(F("tags"), ["python"]),
        first=ArrayIndex(F("tags"), 1),
        length=ArrayLength(F("tags")),
    ).values("contains", "key", "overlap", "members", "first", "length")
    compiled = qs._compiler.compile(qs.state).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "@>" in sql and "&&" in sql and "array_length" in sql
    assert "secret-value" not in sql and "user' or true" not in sql
    assert {"safe": "secret-value"} in compiled.params.values()
    assert "user' or true --" in compiled.params.values()
    assert ["mysql", "postgres"] in compiled.params.values()


@pytest.mark.parametrize("dialect", [sqlite.dialect(), mysql.dialect()])
def test_postgresql_only_expressions_reject_other_dialects(dialect):
    qs = Stage6PGModel.objects.annotate(
        object(),
        has_key=JSONHasKey(F("payload"), "user"),
    ).values("has_key")
    with pytest.raises(CompileError, match="only on PostgreSQL"):
        qs.sql(dialect=dialect)
    array_qs = Stage6PGModel.objects.annotate(
        object(),
        overlap=ArrayOverlap(F("tags"), ["python"]),
    ).values("overlap")
    with pytest.raises(CompileError, match="only on PostgreSQL"):
        array_qs.sql(dialect=dialect)


@pytest.mark.parametrize(
    "dialect", [sqlite.dialect(), postgresql.dialect(), mysql.dialect()]
)
def test_window_set_operations_and_seek_compile_on_three_dialects(dialect):
    base = Stage6JSONModel.objects.all(object())
    ranked = base.annotate(
        row_number=Window(
            RowNumber(),
            partition_by=[F("category")],
            order_by=[F("score").desc(), F("id").asc()],
        )
    ).values("id", "row_number")
    sql = ranked.sql(dialect=dialect).upper()
    assert "ROW_NUMBER() OVER" in sql
    assert "PARTITION BY" in sql and "ORDER BY" in sql

    # A shared session is required for set operations; each query stays lazy.
    session = object()
    left = Stage6JSONModel.objects.filter(session, score__lte=30).values_list(
        "id", flat=True
    )
    right = Stage6JSONModel.objects.filter(session, score__gte=15).values_list(
        "id", flat=True
    )
    for query, operator in (
        (left.union(right), "UNION"),
        (left.union(right, all=True), "UNION ALL"),
        (left.intersection(right), "INTERSECT"),
        (left.difference(right), "EXCEPT"),
    ):
        assert operator in query.sql(dialect=dialect).upper()

    keyset = (
        Stage6JSONModel.objects.all(object()).order_by("-score", "id").seek_after(20, 2)
    )
    compiled = keyset._compiler.compile(keyset.state).compile(dialect=dialect)
    assert 20 in compiled.params.values() and 2 in compiled.params.values()
    assert re.search(r"(?<![\w])(?:20|2)(?![\w])", str(compiled)) is None


def test_set_operations_reject_conflicting_annotation_result_types():
    from async_orm_kit import Value
    from async_orm_kit.exceptions import QueryStateError

    session = object()
    numbers = Stage6JSONModel.objects.annotate(session, chosen=Value(3)).values_list(
        "chosen", flat=True
    )
    strings = Stage6JSONModel.objects.annotate(session, chosen=Value("3")).values_list(
        "chosen", flat=True
    )
    with pytest.raises(QueryStateError, match="incompatible result types"):
        numbers.union(strings)
    with pytest.raises(QueryStateError, match="incompatible result types"):
        numbers.intersection(strings)

    other_numbers = Stage6JSONModel.objects.annotate(
        session, chosen=Value(99)
    ).values_list("chosen", flat=True)
    assert "UNION" in numbers.union(other_numbers).sql(dialect=sqlite.dialect())
