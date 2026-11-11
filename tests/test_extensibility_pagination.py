"""Stage 4: execution-level extensibility and keyset pagination contracts."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    inspect as sa_inspect,
)
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from async_orm_kit import AggregateFunction, F, Function, Model
from async_orm_kit.exceptions import (
    FilterError,
    InvalidLookupError,
    ModelAttributeError,
    QueryStateError,
)


class ExtItem(Model):
    __tablename__ = "stage4_ext_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    group: Mapped[str] = mapped_column(String(20))
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExtParent(Model):
    __tablename__ = "stage4_ext_parents"
    id: Mapped[int] = mapped_column(primary_key=True)
    children: Mapped[list[ExtChild]] = relationship(back_populates="parent")


class ExtChild(Model):
    __tablename__ = "stage4_ext_children"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("stage4_ext_parents.id"))
    score: Mapped[int] = mapped_column(Integer)
    parent: Mapped[ExtParent] = relationship(back_populates="children")


class SyncAsyncAdapter:
    """Executes actual SQLite SQL without claiming native async driver support."""

    def __init__(self, session: Session):
        self.session = session
        self.bind = session.get_bind()

    async def execute(self, statement, parameters=None):
        return self.session.execute(statement, parameters or {})


def execute(query):
    return asyncio.run(query._fetch())


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ExtItem.__table__.create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                ExtItem(id=1, group="a", score=10, note="first"),
                ExtItem(id=2, group="a", score=10, note="second"),
                ExtItem(id=3, group="b", score=20, note=None),
                ExtItem(id=4, group="b", score=20, note="fourth"),
                ExtItem(id=5, group="a", score=30, note="fifth"),
            ]
        )
        session.commit()
        yield SyncAsyncAdapter(session)
    engine.dispose()


def divisible_by(column, value):
    return column % value == 0


def test_custom_lookup_is_opt_in_scoped_and_executable(db):
    basic = ExtItem.objects.all(db)
    with_lookup = basic.with_lookup("multiple_of", divisible_by)
    assert execute(with_lookup.filter(score__multiple_of=20).order_by("id"))[0].id == 3
    assert [
        row.id
        for row in execute(with_lookup.filter(score__multiple_of=20).order_by("id"))
    ] == [3, 4]
    assert "multiple_of" not in basic.state.custom_lookups
    with pytest.raises((InvalidLookupError, ModelAttributeError, FilterError)):
        basic.filter(score__multiple_of=20)


def test_custom_lookups_support_q_composition(db):
    from async_orm_kit import Q

    query = (
        ExtItem.objects.all(db)
        .with_lookups(multiple_of=divisible_by)
        .filter(Q(score__multiple_of=20) | Q(group="a"))
        .order_by("id")
    )
    assert [row.id for row in execute(query)] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    "name", ["", "_private", "score__multiple_of", "bad-name", "exact", "startswith"]
)
def test_custom_lookup_rejects_unsafe_or_reserved_names(db, name):
    with pytest.raises(ValueError):
        ExtItem.objects.all(db).with_lookup(name, divisible_by)


def test_custom_lookup_validates_handler_and_boolean_result(db):
    with pytest.raises(TypeError):
        ExtItem.objects.all(db).with_lookup("bad", "not callable")
    with pytest.raises(InvalidLookupError, match="boolean"):
        ExtItem.objects.all(db).with_lookup(
            "arithmetic", lambda col, value: col + value
        ).filter(score__arithmetic=3)


def test_custom_lookup_uses_bound_parameters_in_each_dialect(db):
    query = (
        ExtItem.objects.all(db)
        .with_lookup("multiple_of", divisible_by)
        .filter(score__multiple_of=21)
    )
    for dialect in [sqlite.dialect(), postgresql.dialect(), mysql.dialect()]:
        sql = query.sql(dialect=dialect)
        assert "21" not in sql
        assert "score" in sql


def test_custom_sql_function_compiles_and_executes(db):
    query = (
        ExtItem.objects.annotate(
            db,
            start=Function(
                lambda col, length: func.substr(col, 1, length), F("group"), 1
            ),
        )
        .values("id", "start")
        .order_by("id")
    )
    assert execute(query) == [
        {"id": 1, "start": "a"},
        {"id": 2, "start": "a"},
        {"id": 3, "start": "b"},
        {"id": 4, "start": "b"},
        {"id": 5, "start": "a"},
    ]
    with pytest.raises(TypeError):
        Function("substr", F("group"))
    with pytest.raises(TypeError, match="ColumnElement"):
        ExtItem.objects.annotate(db, bad=Function(lambda col: object(), F("group")))


def test_custom_aggregate_tracks_grouping_and_terminal_aggregation(db):
    def custom(column):
        return func.total(column)

    grouped = (
        ExtItem.objects.values(db, "group")
        .annotate(total=AggregateFunction("score", custom))
        .order_by("group")
    )
    assert execute(grouped) == [
        {"group": "a", "total": 50.0},
        {"group": "b", "total": 40.0},
    ]

    result = asyncio.run(
        ExtItem.objects.all(db).aggregate(total=AggregateFunction("score", custom))
    )
    assert result == {"total": 90.0}
    with pytest.raises(TypeError):
        AggregateFunction("score", "unsafe")
    with pytest.raises(TypeError, match="ColumnElement"):
        ExtItem.objects.annotate(db, bad=AggregateFunction("score", lambda col: 123))


def test_custom_aggregate_is_detected_in_nested_arithmetic(db):
    query = (
        ExtItem.objects.values(db, "group")
        .annotate(total=AggregateFunction("score", lambda col: func.total(col)) + 1)
        .order_by("group")
    )
    assert execute(query) == [
        {"group": "a", "total": 51.0},
        {"group": "b", "total": 41.0},
    ]


def test_defer_reduces_selected_columns_and_clones_without_mutation(db):
    base = ExtItem.objects.all(db)
    query = base.defer("note")
    assert base.state.deferred_fields == []
    assert query.state.deferred_fields == ["note"]
    assert "note" not in query.sql(dialect=sqlite.dialect()).split("FROM")[0]
    records = execute(query.order_by("id"))
    assert "note" in sa_inspect(records[0]).unloaded
    db.session.refresh(records[0], ["note"])
    assert records[0].note == "first"


def test_defer_can_accumulate_fields(db):
    query = ExtItem.objects.all(db).defer("note").defer("group")
    assert query.state.deferred_fields == ["note", "group"]
    assert [row.id for row in execute(query.order_by("id"))] == [1, 2, 3, 4, 5]


def test_defer_rejects_ambiguous_and_invalid_fields(db):
    base = ExtItem.objects.all(db)
    for name in ("id", "pk"):
        with pytest.raises((QueryStateError, ModelAttributeError)):
            base.defer(name)
    with pytest.raises(ValueError):
        base.defer("group__name")
    with pytest.raises(ModelAttributeError):
        base.defer("absent")
    with pytest.raises(QueryStateError):
        base.defer("note").only("group")
    with pytest.raises(QueryStateError):
        base.only("group").defer("note")
    with pytest.raises(QueryStateError):
        base.defer("note").values("group")
    with pytest.raises(QueryStateError):
        base.values("group").defer("note")


def test_seek_after_and_before_have_correct_boundaries(db):
    base = ExtItem.objects.all(db).order_by("score", "id")
    assert [row.id for row in execute(base.seek_after(10, 1).limit(2))] == [2, 3]
    assert [row.id for row in execute(base.seek_after(10, 2))] == [3, 4, 5]
    assert [row.id for row in execute(base.seek_before(20, 4))] == [1, 2, 3]
    assert [row.id for row in execute(base)] == [1, 2, 3, 4, 5]


def test_seek_supports_mixed_directions_and_projection(db):
    base = ExtItem.objects.values(db, "id", "score").order_by("-score", "id")
    assert execute(base.seek_after(20, 3)) == [
        {"id": 4, "score": 20},
        {"id": 1, "score": 10},
        {"id": 2, "score": 10},
    ]
    assert execute(base.seek_before(20, 3)) == [{"id": 5, "score": 30}]


def test_seek_queries_are_parameterized_on_all_three_dialects(db):
    query = ExtItem.objects.all(db).order_by("score", "id").seek_after(90042, 90043)
    for dialect in [sqlite.dialect(), postgresql.dialect(), mysql.dialect()]:
        sql = query.sql(dialect=dialect)
        assert "90042" not in sql
        assert "90043" not in sql


def test_seek_enforces_stable_ordering_and_rejects_unsupported_shapes(db):
    base = ExtItem.objects.all(db)
    with pytest.raises(QueryStateError, match="order_by"):
        base.seek_after(1)
    with pytest.raises(QueryStateError, match="primary-key"):
        base.order_by("score").seek_after(10)
    with pytest.raises(QueryStateError, match="one cursor value"):
        base.order_by("score", "id").seek_after(10)
    with pytest.raises(ValueError, match="NULL"):
        base.order_by("score", "id").seek_after(None, 1)
    with pytest.raises(TypeError, match="scalar"):
        base.order_by("score", "id").seek_after([10], 1)
    with pytest.raises(QueryStateError, match="non-nullable"):
        base.order_by("note", "id").seek_after("first", 1)
    with pytest.raises(QueryStateError, match="OFFSET"):
        base.order_by("id").offset(1).seek_after(1)
    with pytest.raises(QueryStateError, match="OFFSET"):
        base.order_by("id").seek_after(1).offset(1)
    with pytest.raises(QueryStateError, match="one seek"):
        base.order_by("id").seek_after(1).seek_before(2)
    with pytest.raises(QueryStateError, match="ordering"):
        base.order_by("id").seek_after(1).order_by("-id")
    with pytest.raises(QueryStateError, match="DISTINCT"):
        base.order_by("id").distinct().seek_after(1)
    with pytest.raises(QueryStateError, match="grouped"):
        from async_orm_kit import Count

        base.annotate(total=Count("id")).order_by("id").seek_after(1)


def test_seek_rejects_collection_join_filters_that_multiply_sql_rows(db):
    query = ExtParent.objects.filter(db, children__score__gte=10).order_by("id")
    with pytest.raises(QueryStateError, match="collection JOIN"):
        query.seek_after(1)
