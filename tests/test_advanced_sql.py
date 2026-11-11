"""Execution and dialect contract for Stage 3 extended SQL features."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import JSON, Integer, String, create_engine, func, literal, select
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import CompileError
from sqlalchemy.orm import Mapped, Session, mapped_column

from async_orm_kit import (
    ArrayContains,
    ArrayIndex,
    ArrayLength,
    ArrayOverlap,
    CompoundQuerySet,
    F,
    JSONContains,
    JSONHasKey,
    JSONPath,
    Model,
)
from async_orm_kit.exceptions import QueryStateError


class SQLItem(Model):
    __tablename__ = "stage3_sql_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(40))
    score: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class PgSQLItem(Model):
    __tablename__ = "stage3_pg_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String))


class Adapter:
    """Synchronous SQLite connection behind an async-shaped execution adapter."""

    def __init__(self, session):
        self._session = session
        self.bind = session.get_bind()

    async def execute(self, statement, params=None):
        return self._session.execute(statement, params or {})


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    SQLItem.__table__.create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                SQLItem(
                    id=1,
                    label="alpha",
                    score=10,
                    payload={"user": {"name": "Ada", "age": 35}, "active": True},
                ),
                SQLItem(
                    id=2,
                    label="beta",
                    score=20,
                    payload={"user": {"name": "Grace", "age": 41}, "active": False},
                ),
                SQLItem(
                    id=3,
                    label="gamma",
                    score=30,
                    payload={"user": {"name": "Ada", "age": 50}, "active": True},
                ),
            ]
        )
        session.commit()
        yield Adapter(session)
    engine.dispose()


def run(awaitable):
    async def consume():
        return await awaitable

    return asyncio.run(consume())


def test_json_path_nested_scalar_values_and_filters_execute(db):
    query = (
        SQLItem.objects.annotate(
            db, name=JSONPath(F("payload"), "user", "name", as_type="string")
        )
        .filter(name="Ada")
        .values("label", "name")
        .order_by("label")
    )
    assert run(query._fetch()) == [
        {"label": "alpha", "name": "Ada"},
        {"label": "gamma", "name": "Ada"},
    ]
    ages = (
        SQLItem.objects.annotate(
            db, age=JSONPath(F("payload"), "user", "age", as_type="integer")
        )
        .filter(age__gte=40)
        .values_list("label")
        .order_by("age")
    )
    assert run(ages._fetch()) == [("beta", 41), ("gamma", 50)]


def test_json_path_uses_dialect_specific_sql_and_bound_paths(db):
    queryset = SQLItem.objects.annotate(
        db, value=JSONPath(F("payload"), "user", "name", as_type="string")
    ).values("value")
    for dialect, token in [
        (sqlite.dialect(), "JSON_EXTRACT"),
        (mysql.dialect(), "JSON_EXTRACT"),
        (postgresql.dialect(), "#>>"),
    ]:
        sql = queryset.sql(dialect=dialect)
        assert token in sql.upper()
        assert "user" not in sql and "name" not in sql


def test_json_path_validates_source_type_and_segments(db):
    with pytest.raises(ValueError, match="path segment"):
        JSONPath(F("payload"))
    with pytest.raises(ValueError, match="non-empty"):
        JSONPath(F("payload"), "")
    with pytest.raises(ValueError, match="non-empty"):
        JSONPath(F("payload"), True)
    with pytest.raises(ValueError, match="result type"):
        JSONPath(F("payload"), "x", as_type="python")
    with pytest.raises(TypeError, match="JSON"):
        SQLItem.objects.annotate(db, value=JSONPath(F("score"), "x"))


def test_postgres_jsonb_operators_are_parameterized_and_dialect_guarded(db):
    query = PgSQLItem.objects.annotate(
        db,
        contains=JSONContains(F("payload"), {"active": True}),
        has_key=JSONHasKey(F("payload"), "secret'or 1=1 --"),
    ).values("contains", "has_key")
    sql = query.sql(dialect=postgresql.dialect())
    assert "@>" in sql and "?" in sql
    assert "secret'or 1=1" not in sql
    with pytest.raises(CompileError, match="only on PostgreSQL"):
        query.sql(dialect=sqlite.dialect())
    with pytest.raises(CompileError, match="only on PostgreSQL"):
        query.sql(dialect=mysql.dialect())
    with pytest.raises(TypeError, match="JSONB"):
        SQLItem.objects.annotate(db, invalid=JSONContains(F("payload"), {"x": 1}))
    with pytest.raises(TypeError, match="JSONB"):
        SQLItem.objects.annotate(db, invalid=JSONHasKey(F("payload"), "x"))


def test_postgres_array_operators_are_typed_and_dialect_guarded(db):
    query = PgSQLItem.objects.annotate(
        db,
        first=ArrayIndex(F("tags"), 1),
        count=ArrayLength(F("tags")),
        contains=ArrayContains(F("tags"), ["python"]),
        overlaps=ArrayOverlap(F("tags"), ["sql", "api"]),
    ).values("first", "count", "contains", "overlaps")
    sql = query.sql(dialect=postgresql.dialect())
    assert "array_length" in sql.lower()
    assert "@>" in sql and "&&" in sql
    assert "[" in sql
    with pytest.raises(CompileError, match="only on PostgreSQL"):
        query.sql(dialect=sqlite.dialect())
    with pytest.raises(TypeError, match="ARRAY"):
        SQLItem.objects.annotate(db, invalid=ArrayLength(F("payload")))
    with pytest.raises(ValueError, match="start at 1"):
        PgSQLItem.objects.annotate(db, invalid=ArrayIndex(F("tags"), 0))
    with pytest.raises(TypeError, match="list or tuple"):
        ArrayOverlap(F("tags"), "python")


def test_union_and_union_all_execute_with_projection_shapes(db):
    first = SQLItem.objects.filter(db, score__lte=20).values_list("label", flat=True)
    second = SQLItem.objects.filter(db, score__gte=20).values_list("label", flat=True)
    unioned = first.union(second).order_by("-label")
    assert isinstance(unioned, CompoundQuerySet)
    assert run(unioned) == ["gamma", "beta", "alpha"]
    assert run(unioned.count()) == 3
    assert run(unioned.exists()) is True
    assert run(unioned.limit(1).count()) == 1
    assert run(unioned.offset(50).exists()) is False
    assert run(unioned.first()) == "gamma"
    assert run(first.union(second, all=True).count()) == 4
    assert run(first.union(second, all=True).order_by("label").limit(2).offset(1)) == [
        "beta",
        "beta",
    ]
    assert run(first.union(second, all=True).count()) == 4


def test_intersection_difference_and_values_execute(db):
    left = SQLItem.objects.filter(db, score__gte=20).values("label")
    right = SQLItem.objects.filter(db, score__lte=20).values("label")
    assert run(left.intersection(right)) == [{"label": "beta"}]
    assert run(left.difference(right)) == [{"label": "gamma"}]
    assert run(right.difference(left)) == [{"label": "alpha"}]


def test_model_mode_union_hydrates_identity_mapped_models(db):
    left = SQLItem.objects.filter(db, score__lt=30)
    right = SQLItem.objects.filter(db, score__gt=10)
    result = run(left.union(right).order_by("id"))
    assert [item.id for item in result] == [1, 2, 3]
    assert all(isinstance(item, SQLItem) for item in result)
    assert run(left.union(right).count()) == 3


def test_set_operations_reject_incompatible_shapes_and_sessions(db):
    session_other = Adapter(db._session)
    left = SQLItem.objects.values(db, "label")
    with pytest.raises(ValueError, match="same AsyncSession"):
        left.union(SQLItem.objects.values(session_other, "label"))
    with pytest.raises(QueryStateError, match="matching projections"):
        left.union(SQLItem.objects.values(db, "score"))
    with pytest.raises(QueryStateError, match="branches cannot"):
        left.union(SQLItem.objects.values(db, "label").order_by("label"))
    with pytest.raises(ValueError, match="unknown column"):
        left.union(SQLItem.objects.values(db, "label")).order_by("label;DROP TABLE")
    with pytest.raises(ValueError, match="at least one"):
        left.union()
    with pytest.raises(ValueError, match="nonnegative"):
        left.union(SQLItem.objects.values(db, "label")).limit(-1)


def test_cte_read_and_recursive_cte_execute(db):
    base = SQLItem.objects.filter(db, score__gte=20).values_list("score", flat=True)
    cte = base.cte("big_scores")
    assert run(db.execute(select(func.sum(cte.c.score)))).scalar_one() == 50

    # Recursive term is composed with SQLAlchemy's public CTE API. The wrapper
    # never manually interpolates SQL fragments.
    integers = select(literal(1).label("n")).cte("integers", recursive=True)
    integers = integers.union_all(
        select((integers.c.n + 1).label("n")).where(integers.c.n < 4)
    )
    assert run(
        db.execute(select(integers.c.n).order_by(integers.c.n))
    ).scalars().all() == [1, 2, 3, 4]
    with pytest.raises(ValueError, match="valid SQL identifier"):
        base.cte("unsafe; drop table")


def test_set_query_sql_parameterization_and_cte_interop(db):
    left = SQLItem.objects.filter(db, label="hello' OR 1=1 --").values("label")
    right = SQLItem.objects.values(db, "label")
    combined = left.union(right)
    assert "hello' OR 1=1" not in combined.sql(dialect=sqlite.dialect())
    alias = combined.cte("combined_labels")
    assert run(db.execute(select(func.count()).select_from(alias))).scalar_one() == 3


def test_set_operation_sql_compiles_multiple_dialects(db):
    left = SQLItem.objects.filter(db, score__gte=10).values("label")
    right = SQLItem.objects.filter(db, score__lte=30).values("label")
    for dialect in (sqlite.dialect(), postgresql.dialect(), mysql.dialect()):
        for combined in (
            left.union(right),
            left.union(right, all=True),
            left.intersection(right),
            left.difference(right),
        ):
            sql = combined.sql(dialect=dialect)
            assert "SELECT" in sql.upper()
            assert "label" in sql


def test_json_boolean_and_nested_object_projection_execute(db):
    bool_query = (
        SQLItem.objects.annotate(
            db,
            enabled=JSONPath(F("payload"), "active", as_type="boolean"),
        )
        .filter(enabled=True)
        .values_list("label", flat=True)
        .order_by("label")
    )
    assert run(bool_query._fetch()) == ["alpha", "gamma"]
    nested = (
        SQLItem.objects.annotate(db, person=JSONPath(F("payload"), "user"))
        .filter(label="alpha")
        .values("person")
    )
    assert run(nested._fetch())[0]["person"] == {"name": "Ada", "age": 35}


def test_postgres_predicate_expressions_can_be_used_directly_in_filter(db):
    queryset = PgSQLItem.objects.filter(
        db,
        JSONContains(F("payload"), {"active": True}),
        ArrayContains(F("tags"), ["python"]),
    )
    sql = queryset.sql(dialect=postgresql.dialect())
    assert "@>" in sql
    with pytest.raises(CompileError, match="only on PostgreSQL"):
        queryset.sql(dialect=mysql.dialect())
    with pytest.raises(TypeError, match="boolean predicates"):
        SQLItem.objects.filter(db, JSONPath(F("payload"), "user"))


def test_json_projection_of_explicit_annotation_does_not_duplicate_columns(db):
    queryset = SQLItem.objects.annotate(
        db,
        age=JSONPath(F("payload"), "user", "age", as_type="integer"),
    ).values_list("age", flat=True)
    assert len(queryset.compiler.compile(queryset.state).selected_columns) == 1
    assert sorted(run(queryset._fetch())) == [35, 41, 50]

    dictionary = (
        SQLItem.objects.annotate(
            db,
            age=JSONPath(F("payload"), "user", "age", as_type="integer"),
        )
        .values("label", "age")
        .order_by("label")
    )
    assert len(dictionary.compiler.compile(dictionary.state).selected_columns) == 2
    assert run(dictionary._fetch()) == [
        {"label": "alpha", "age": 35},
        {"label": "beta", "age": 41},
        {"label": "gamma", "age": 50},
    ]


def test_recursive_cte_can_begin_from_queryset_projection(db):
    recursive = (
        SQLItem.objects.filter(db, id=1)
        .values_list("id", flat=True)
        .cte("linked_ids", recursive=True)
    )
    recursive = recursive.union_all(
        select(SQLItem.id)
        .join(recursive, SQLItem.id == recursive.c.id + 1)
        .where(SQLItem.id <= 3)
    )
    assert run(
        db.execute(select(recursive.c.id).order_by(recursive.c.id))
    ).scalars().all() == [1, 2, 3]


def test_empty_set_operation_and_validation(db):
    base = SQLItem.objects.values(db, "label")
    none = SQLItem.objects.filter(db, score__lt=0).values("label")
    combined = none.intersection(base)
    assert run(combined) == []
    assert run(combined.count()) == 0
    assert run(combined.exists()) is False
    with pytest.raises(QueryStateError, match="row locking"):
        SQLItem.objects.all(db).select_for_update().union(SQLItem.objects.all(db))


def test_postgresql_jsonb_array_predicates_support_exclude_without_raw_sql(db):
    selected = PgSQLItem.objects.exclude(
        db,
        JSONHasKey(F("payload"), "private"),
        ArrayOverlap(F("tags"), ["internal"]),
    )
    sql = selected.sql(dialect=postgresql.dialect())
    assert "IS NOT true" in sql or "IS NOT TRUE" in sql
    assert "?" in sql and "&&" in sql
    with pytest.raises(CompileError, match="only on PostgreSQL"):
        selected.sql(dialect=sqlite.dialect())


def test_jsonpath_explicit_comparison_works_without_annotation(db):
    selected = SQLItem.objects.filter(
        db, JSONPath(F("payload"), "user", "name", as_type="string").eq("Ada")
    ).order_by("label")
    assert [obj.label for obj in run(selected._fetch())] == ["alpha", "gamma"]


def test_compound_stream_closes_result_and_preserves_order(db):
    class StreamingResult:
        def __init__(self, rows):
            self._rows = iter(rows)
            self.closed = False

        def unique(self):
            return self

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._rows)
            except StopIteration:
                raise StopAsyncIteration from None

        async def close(self):
            self.closed = True

    class StreamingAdapter(Adapter):
        last_result = None

        async def stream(self, statement):
            self.last_result = StreamingResult(self._session.execute(statement))
            return self.last_result

    adapter = StreamingAdapter(db._session)
    left = SQLItem.objects.filter(adapter, score__lt=25).values_list("label", flat=True)
    right = SQLItem.objects.filter(adapter, score__gte=25).values_list(
        "label", flat=True
    )

    async def consume_early():
        iterator = left.union(right).order_by("label").stream()
        first = await anext(iterator)
        await iterator.aclose()
        return first

    assert run(consume_early()) == "alpha"
    assert adapter.last_result.closed

    async def consume_all():
        return [item async for item in left.union(right).order_by("label")]

    assert run(consume_all()) == ["alpha", "beta", "gamma"]
    assert adapter.last_result.closed
