"""Native result-shape and database-boundary failure contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.exc import SQLAlchemyError
from test_model_lifecycle_contracts import (
    LifecycleRecord,
    lifecycle_sessions as lifecycle_sessions,
)

from async_orm_kit import F, QuerySet
from async_orm_kit.exceptions import (
    DatabaseError,
    ModelAttributeError,
    QueryStateError,
    RelationshipError,
)
from async_orm_kit.queryset.executor import QueryExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape", ["model", "annotated", "dict", "annotated_dict", "tuple", "flat"]
)
@pytest.mark.parametrize("empty", [False, True])
async def test_stream_result_shapes_and_empty_results(lifecycle_sessions, shape, empty):
    async with lifecycle_sessions() as session:
        session.add_all(
            [
                LifecycleRecord(id=1, name="one", score=2),
                LifecycleRecord(id=2, name="two", score=3),
            ]
        )
        await session.flush()
        query = LifecycleRecord.objects.order_by(session, "id")
        if empty:
            query = query.filter(id=99)
        if shape in {"annotated", "annotated_dict"}:
            query = query.annotate(doubled=F("score") * 2, tripled=F("score") * 3)
        if shape == "dict":
            query = query.values("id")
        elif shape == "annotated_dict":
            query = query.values("id", "doubled")
        elif shape == "tuple":
            query = query.values_list("id", "name")
        elif shape == "flat":
            query = query.values_list("id", flat=True)
        rows = [row async for row in query.stream()]
        if empty:
            assert rows == []
        elif shape == "dict":
            assert rows == [{"id": 1}, {"id": 2}]
        elif shape == "annotated_dict":
            assert rows == [
                {"id": 1, "doubled": 4, "tripled": 6},
                {"id": 2, "doubled": 6, "tripled": 9},
            ]
        elif shape == "tuple":
            assert rows == [(1, "one"), (2, "two")]
        elif shape == "flat":
            assert rows == [1, 2]
        else:
            assert [row.id for row in rows] == [1, 2]
            if shape == "annotated":
                assert [row.doubled for row in rows] == [4, 6]


@pytest.mark.asyncio
async def test_get_shapes_and_model_specific_cardinality_errors(lifecycle_sessions):
    async with lifecycle_sessions() as session:
        session.add_all(
            [
                LifecycleRecord(id=1, name="one", score=2),
                LifecycleRecord(id=2, name="two", score=3),
            ]
        )
        await session.flush()
        query = LifecycleRecord.objects.filter(session, id=1)
        assert await query.values_list("name").get() == ("one",)
        assert await query.values_list("name", flat=True).get() == "one"
        row = await query.annotate(doubled=F("score") * 2).get()
        assert row.id == 1 and row.doubled == 4
        with pytest.raises(LifecycleRecord.DoesNotExist):
            await query.filter(id=99).get()
        with pytest.raises(LifecycleRecord.MultipleObjectsReturned):
            await LifecycleRecord.objects.all(session).get()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["fetch_all", "get", "count", "exists", "first"])
async def test_execution_failures_are_chained_and_sessions_remain_caller_owned(
    lifecycle_sessions, monkeypatch, method
):
    async with lifecycle_sessions() as session:
        failure = SQLAlchemyError("driver unavailable")
        monkeypatch.setattr(session, "execute", AsyncMock(side_effect=failure))
        executor = QueryExecutor(LifecycleRecord.objects.all(session))
        with pytest.raises(DatabaseError) as error:
            await getattr(executor, method)()
        root = error.value
        while isinstance(root, DatabaseError):
            root = root.cause
        assert root is failure


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [ModelAttributeError("bad field"), DatabaseError("already wrapped")]
)
async def test_get_preserves_known_exceptions(lifecycle_sessions, monkeypatch, failure):
    async with lifecycle_sessions() as session:
        query = LifecycleRecord.objects.all(session)
        monkeypatch.setattr(
            query.compiler, "compile", lambda state: (_ for _ in ()).throw(failure)
        )
        with pytest.raises(type(failure)) as error:
            await query.get()
        assert error.value is failure


@pytest.mark.asyncio
async def test_fetch_preserves_relationship_errors(lifecycle_sessions, monkeypatch):
    async with lifecycle_sessions() as session:
        query = LifecycleRecord.objects.all(session)
        failure = RelationshipError("bad path")
        monkeypatch.setattr(
            query.compiler, "compile", lambda state: (_ for _ in ()).throw(failure)
        )
        with pytest.raises(RelationshipError) as error:
            await query
        assert error.value is failure


@pytest.mark.asyncio
async def test_stream_acquisition_failure_and_close_failure_are_observable(
    lifecycle_sessions, monkeypatch, caplog
):
    async with lifecycle_sessions() as session:
        with monkeypatch.context() as patch:
            patch.setattr(
                session,
                "stream",
                AsyncMock(side_effect=SQLAlchemyError("stream acquisition")),
            )
            with pytest.raises(DatabaseError):
                await anext(LifecycleRecord.objects.all(session).stream())
        session.add(LifecycleRecord(name="one"))
        await session.flush()
        query = LifecycleRecord.objects.all(session)
        result = await session.stream(query.compiler.compile(query.state))
        close = result.close
        monkeypatch.setattr(
            result, "close", AsyncMock(side_effect=RuntimeError("close failed"))
        )
        monkeypatch.setattr(session, "stream", AsyncMock(return_value=result))
        assert len([row async for row in query.stream()]) == 1
        assert "Failed to close" in caplog.text
        await close()


@pytest.mark.parametrize("active", [False, None, True])
def test_row_lock_requires_supported_active_transaction(active):
    session = SimpleNamespace(
        get_bind=lambda **kwargs: SimpleNamespace(dialect=postgresql.dialect())
    )
    if active is not None:
        session.in_transaction = lambda: active
    query = QuerySet(LifecycleRecord, session).select_for_update()
    if active:
        assert QueryExecutor(query)._ensure_lock_support() is None
    else:
        with pytest.raises(QueryStateError):
            QueryExecutor(query)._ensure_lock_support()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dialect,analyze,verbose,prefix",
    [
        (postgresql.dialect(), False, False, "EXPLAIN SELECT"),
        (postgresql.dialect(), True, False, "EXPLAIN (ANALYZE)"),
        (postgresql.dialect(), False, True, "EXPLAIN (VERBOSE)"),
        (postgresql.dialect(), True, True, "EXPLAIN (ANALYZE, VERBOSE)"),
        (mysql.dialect(), False, False, "EXPLAIN SELECT"),
        (mysql.dialect(), True, False, "EXPLAIN ANALYZE"),
        (sqlite.dialect(), False, False, "EXPLAIN QUERY PLAN"),
    ],
)
async def test_explain_options_preserve_bound_parameters(
    dialect, analyze, verbose, prefix
):
    connection = SimpleNamespace(
        exec_driver_sql=AsyncMock(
            return_value=SimpleNamespace(
                fetchall=lambda: [("plan one",), ("plan two",)]
            )
        )
    )
    session = SimpleNamespace(
        get_bind=lambda **kwargs: SimpleNamespace(dialect=dialect),
        connection=AsyncMock(return_value=connection),
    )
    secret = "private'; DROP TABLE records; --"
    query = QuerySet(LifecycleRecord, session).filter(name=secret)
    assert await query.explain(analyze=analyze, verbose=verbose) == "plan one\nplan two"
    statement, parameters = connection.exec_driver_sql.call_args.args
    assert (
        statement.startswith(prefix)
        and secret not in statement
        and secret in str(parameters)
    )
    assert secret not in query.sql()


@pytest.mark.asyncio
async def test_explain_unsupported_options_and_driver_failure():
    for name, analyze, verbose, error in [
        ("sqlite", True, False, NotImplementedError),
        ("sqlite", False, True, NotImplementedError),
        ("mysql", False, True, NotImplementedError),
        ("mariadb", True, False, NotImplementedError),
        ("mariadb", False, True, NotImplementedError),
        ("other", False, False, NotImplementedError),
        ("sqlite", 1, False, TypeError),
        ("sqlite", False, 1, TypeError),
    ]:
        dialect = sqlite.dialect()
        dialect.name = name
        session = SimpleNamespace(
            get_bind=lambda dialect=dialect: SimpleNamespace(dialect=dialect)
        )
        with pytest.raises(error):
            await QuerySet(LifecycleRecord, session).explain(analyze, verbose)
    dialect = mysql.dialect()
    dialect.name = "mariadb"
    session = SimpleNamespace(
        get_bind=lambda dialect=dialect: SimpleNamespace(dialect=dialect),
        connection=AsyncMock(side_effect=SQLAlchemyError("connection lost")),
    )
    with pytest.raises(DatabaseError, match="explaining"):
        await QuerySet(LifecycleRecord, session).explain()


def test_queryset_requires_model_and_session():
    with pytest.raises(ValueError):
        QuerySet(None, object())
    with pytest.raises(ValueError):
        QuerySet(LifecycleRecord, None)
    assert "LifecycleRecord" in repr(QuerySet(LifecycleRecord, object()))
