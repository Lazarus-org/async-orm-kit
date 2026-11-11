"""Resolver validation, relationship paths, and scalar subquery contracts."""

from types import SimpleNamespace

import pytest
from sqlalchemy import literal, select
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import aliased
from test_advanced_stage7 import Stage7CompositeParent
from test_model_lifecycle_contracts import LifecycleRecord
from test_relationship_planning import Phase4Post, Phase4User

from async_orm_kit import Q, QuerySet, Subquery, Sum, Value
from async_orm_kit.exceptions import (
    FilterError,
    InvalidLookupError,
    InvalidRelationshipValueError,
    ModelAttributeError,
    QueryStateError,
    RelationshipError,
)
from async_orm_kit.query.compiler import QueryCompiler
from async_orm_kit.query.lookups.conditions import ConditionBuilder
from async_orm_kit.query.lookups.correlated import resolve_q
from async_orm_kit.query.lookups.parser import FilterParser
from async_orm_kit.query.lookups.resolver import FieldResolver, ResolverContext
from async_orm_kit.query.planning.directives import LoadStrategy
from async_orm_kit.queryset.state import QueryState


def test_resolver_paths_pk_aliases_and_invalid_terminal_attributes(monkeypatch):
    resolver = FieldResolver(QueryState(model=Phase4User))
    assert resolver.parse_field_key("unknown__value").is_simple
    assert resolver.parse_field_key("posts__unknown").field_name == "unknown"
    assert resolver.parse_field_key("posts__author").field_name == ""
    assert resolver.resolve_to_column("posts__author").column is not None
    assert resolver.resolve_to_column("posts__pk").column.key == "id"
    with pytest.raises(ModelAttributeError):
        resolver.resolve_to_column("posts__unknown")
    monkeypatch.setattr(Phase4Post, "contract_constant", 7, raising=False)
    with pytest.raises(ModelAttributeError):
        resolver.resolve_to_column("posts__contract_constant")
    node = SimpleNamespace(alias=aliased(Stage7CompositeParent))
    assert len(FieldResolver._resolve_field_on_node(node, "pk").clauses) == 2
    assert FieldResolver._resolve_pk(Phase4User).key == "id"
    with pytest.raises(ModelAttributeError):
        FieldResolver._resolve_pk(Stage7CompositeParent)
    assert str(resolve_q(~Q(posts__pk=1), resolver, ResolverContext()))
    assert resolve_q(Q(), resolver, ResolverContext()) is None


def test_condition_builder_values_and_query_projection_validation():
    session = object()
    resolver = FieldResolver(QueryState(model=LifecycleRecord))
    builder = ConditionBuilder(resolver)
    assert builder._resolve_value(Value(1)).value == 1
    plain = QuerySet(LifecycleRecord, session)
    assert builder._resolve_value(plain.values("id")).selected_columns
    assert builder._resolve_value(plain.values_list("id")).selected_columns
    invalid = [
        plain.values("id", "name"),
        plain.values_list("id", "name"),
        plain.annotate(total=Sum("score")),
        QuerySet(Stage7CompositeParent, session),
    ]
    for query in invalid:
        with pytest.raises(QueryStateError):
            builder._resolve_value(query)
    node = SimpleNamespace(has_nullable_check=False)
    builder._mark_nullable_checks(node, "isnull", True)
    assert node.has_nullable_check
    with pytest.raises(InvalidLookupError):
        builder._validate_lookup("like", SimpleNamespace(like=None))
    relation_builder = ConditionBuilder(FieldResolver(QueryState(model=Phase4Post)))
    with pytest.raises(InvalidRelationshipValueError):
        relation_builder.build(Phase4Post.author, "exact", 42)
    column = SimpleNamespace(
        key="field",
        like=lambda value: (_ for _ in ()).throw(ArgumentError("invalid driver value")),
    )
    with pytest.raises(FilterError):
        builder.build(column, "like", "a")


def test_parser_bad_inputs_and_error_chain(monkeypatch):
    resolver = FieldResolver(QueryState(model=LifecycleRecord))
    parser = FilterParser(resolver)
    for value in [[], {"": 1}, {1: 1}]:
        with pytest.raises(TypeError):
            parser.parse(value)
    failure = RuntimeError("resolver failure")
    monkeypatch.setattr(
        resolver, "resolve_to_column", lambda **kw: (_ for _ in ()).throw(failure)
    )
    with pytest.raises(FilterError) as error:
        parser.parse({"id": 1})
    assert error.value.__cause__ is failure


def test_scalar_subquery_all_projection_paths():
    session = object()
    outer = FieldResolver(QueryState(model=Phase4User))
    q = QuerySet(LifecycleRecord, session)
    for query in [q, q.values("id"), q.values_list("id")]:
        assert "SELECT" in str(Subquery(query).resolve(outer))
    for query in [
        q.values_list("id", "name"),
        q.annotate(total=Sum("score")),
        QuerySet(Stage7CompositeParent, session),
    ]:
        with pytest.raises(QueryStateError):
            Subquery(query).resolve(outer)


def test_loader_chain_and_distinct_compilation_contracts():
    compiler = QueryCompiler()
    for parts, strategies in [
        ([], ()),
        ([""], ()),
        (["posts", "author"], (LoadStrategy.JOINEDLOAD,)),
        (["unknown"], ()),
    ]:
        with pytest.raises(RelationshipError):
            compiler._build_joinedload_chain(Phase4User, parts, strategies)
    assert compiler._apply_group_by(select(literal(1)), []).compare(select(literal(1)))
    q = QuerySet(LifecycleRecord, object())
    assert "DISTINCT" in q.distinct().sql(
        __import__("sqlalchemy").dialects.sqlite.dialect()
    )
    from sqlalchemy.dialects.postgresql import dialect

    assert "DISTINCT ON" in q.distinct("id").sql(dialect())
    assert "GROUP BY" in q.only("id").annotate(total=Sum("score")).sql(dialect())
