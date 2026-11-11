"""JSON extractor typing and PostgreSQL expression contracts."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import JSON, Boolean, Float, Integer, String, column, literal, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from test_relationship_planning import Phase4Post, Phase4User

from async_orm_kit.exceptions import InvalidLookupError
from async_orm_kit.query.lookups.json_paths import json_lookup
from async_orm_kit.query.postgres.optimizer import PostgresOptimizer as PG


@pytest.mark.parametrize(
    "value,expected",
    [(True, Boolean), (3, Integer), (2.5, Float), ("x", String), (None, String)],
)
def test_json_scalar_extractor_types(value, expected):
    expression, operator = json_lookup(column("data", JSON), "nested__value", value)
    assert isinstance(expression.type, expected) and operator == "exact"
    assert "data" in str(expression.compile(dialect=sqlite.dialect()))


@pytest.mark.parametrize(
    "value,expected", [(True, Boolean), (3, Integer), (2.5, Float), ("x", String)]
)
def test_json_membership_and_sql_operand_types(value, expected):
    for operand in ([value], literal(value)):
        expression, operator = json_lookup(
            column("data", JSON),
            "value__in" if isinstance(operand, list) else "value__gte",
            operand,
        )
        assert isinstance(expression.type, expected)
    expression, _ = json_lookup(column("data", JSON), "value__in", [])
    assert isinstance(expression.type, JSON)


def test_json_validation_root_and_fallback():
    doc = column("data", JSON)
    binary = column("binary", JSONB)
    assert json_lookup(doc, "exact", {}) is None
    assert json_lookup(doc, "isnull", True) == (doc, "isnull")
    for lookup in ["a____b", "__a"]:
        with pytest.raises(InvalidLookupError):
            json_lookup(doc, lookup, 1)
    for lookup, value in [
        ("has_key", ""),
        ("has_key", 1),
        ("has_keys", []),
        ("has_keys", [""]),
        ("has_any_keys", 42),
        ("has_any_keys", [2]),
    ]:
        with pytest.raises(TypeError):
            json_lookup(binary, lookup, value)
    with pytest.raises(InvalidLookupError):
        json_lookup(doc, "a__gt", literal(None))
    expr, _ = json_lookup(doc, "a__gt", {})
    assert isinstance(expr.type, JSON)
    for lookup, value in [
        ("has_key", "x"),
        ("has_keys", ["a"]),
        ("has_any_keys", ["b"]),
        ("contained_by", {}),
        ("contains", []),
    ]:
        expression = json_lookup(binary, lookup, value)
        compiled = expression.compile(dialect=postgresql.dialect())
        assert compiled.params and str(compiled)


@pytest.mark.parametrize("descending", [False, True])
def test_postgres_lateral_and_exists_leave_relationship_metadata_unchanged(descending):
    relationship = Phase4User.posts
    before = set(relationship.property.remote_side)
    lateral = PG.lateral_join_top_n(
        Phase4User, Phase4Post, "author_id", "id", 2, descending
    )
    sql = str(select(lateral).compile(dialect=postgresql.dialect()))
    assert "LATERAL" in sql and ("DESC" in sql) == descending
    predicate = PG.optimize_exists_filter(Phase4User, relationship, Phase4Post.id > 1)
    assert "EXISTS" in str(predicate)
    assert set(relationship.property.remote_side) == before


def test_postgres_operators_and_sync_explain_contract():
    assert "@>" in str(PG.jsonb_contains(column("doc", JSONB), {}))
    assert "@>" in str(PG.array_contains(column("items", ARRAY(Integer)), [1]))
    dialect = postgresql.dialect()
    connection = Mock()
    connection.exec_driver_sql.return_value.fetchall.return_value = [
        ("Seq Scan on records",)
    ]
    session = SimpleNamespace(
        bind=SimpleNamespace(dialect=dialect),
        get_bind=lambda: SimpleNamespace(dialect=dialect),
        connection=lambda: connection,
    )
    stmt = select(literal("secret"))
    assert "Seq Scan" in PG.explain_analyze(session, stmt)
    statement, parameters = connection.exec_driver_sql.call_args.args
    assert "secret" not in statement and "secret" in parameters.values()
    assert PG.suggest_index(session, stmt)
    connection.exec_driver_sql.return_value.fetchall.return_value = [("Index Scan",)]
    assert PG.suggest_index(session, stmt) == []
    session.bind.dialect = sqlite.dialect()
    assert (
        PG.explain_analyze(session, stmt) == ""
        and PG.suggest_index(session, stmt) == []
    )
    with pytest.raises(TypeError):
        PG.explain_analyze(session, stmt, verbose=1)
