"""Executable expression algebra and immutable predicate contracts."""

from __future__ import annotations

import operator

import pytest
from sqlalchemy import literal
from test_advanced_expression_correctness import AdvAuthor, db as db

from async_orm_kit import Avg, Case, Count, F, FExpression, Max, Min, Q, Value, When
from async_orm_kit.exceptions import FilterError
from async_orm_kit.query.expressions.case import _resolve_expression
from async_orm_kit.query.lookups.resolver import FieldResolver, ResolverContext
from async_orm_kit.query.q import _freeze_value
from async_orm_kit.queryset.state import QueryState

OPERATIONS = [
    (lambda e: e + 2, lambda x: x + 2),
    (lambda e: e - 2, lambda x: x - 2),
    (lambda e: e * 2, lambda x: x * 2),
    (lambda e: e / 2, lambda x: x / 2),
    (lambda e: e % 2, lambda x: x % 2),
    (lambda e: 2 + e, lambda x: 2 + x),
    (lambda e: 9 - e, lambda x: 9 - x),
    (lambda e: 2 * e, lambda x: 2 * x),
    (lambda e: 12 / e, lambda x: 12 / x),
    (lambda e: 12 % e, lambda x: 12 % x),
    (lambda e: -e, lambda x: -x),
    (lambda e: abs(e), lambda x: abs(x)),
    (lambda e: e // 2, lambda x: x // 2),
    (lambda e: 12 // e, lambda x: 12 // x),
    (lambda e: e & 3, lambda x: x & 3),
    (lambda e: 3 & e, lambda x: 3 & x),
    (lambda e: e | 3, lambda x: x | 3),
    (lambda e: 3 | e, lambda x: 3 | x),
    (lambda e: ~e, lambda x: ~x),
    (lambda e: e**2, lambda x: x**2),
    (lambda e: 2**e, lambda x: 2**x),
    (lambda e: e == 2, lambda x: x == 2),
    (lambda e: e != 2, lambda x: x != 2),
    (lambda e: e < 2, lambda x: x < 2),
    (lambda e: e <= 2, lambda x: x <= 2),
    (lambda e: e > 2, lambda x: x > 2),
    (lambda e: e >= 2, lambda x: x >= 2),
    (lambda e: e.eq(2), lambda x: x == 2),
    (lambda e: e.ne(2), lambda x: x != 2),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("operation,expected", OPERATIONS)
async def test_expression_operators_execute_sql(db, nested, operation, expected):
    expression = F("score") + 5 if nested else F("score")
    value = 6 if nested else 1
    query = (
        AdvAuthor.objects.filter(db, name="Grace")
        .annotate(calculated=operation(expression))
        .values_list("calculated", flat=True)
    )
    assert await query == [expected(value)]


@pytest.mark.asyncio
async def test_expression_operands_aggregates_and_ordering(db):
    resolver = FieldResolver(QueryState(AdvAuthor))
    context = ResolverContext()
    assert +F("score") is not None
    assert repr(F("score")) == "F('score')" and hash(F("score")) == hash("score")
    expression = F("score") + literal(2)
    assert isinstance(hash(expression), int) and "add" in repr(expression)
    assert "pow" in repr(expression**2)
    assert "ASC" in repr(expression.asc()) and "DESC" in repr(expression.desc())
    with pytest.raises(ValueError):
        F("")
    with pytest.raises(ValueError):
        FExpression(None, "unknown", 2)
    with pytest.raises(AttributeError):
        expression.left = 1
    with pytest.raises(TypeError):
        FExpression(F("score"), operator.add, object()).resolve(resolver)
    aggregate = (
        await AdvAuthor.objects.filter(db, name="Grace")
        .annotate(total=F("score") + Count("posts"))
        .values_list("total", flat=True)
    )
    assert aggregate == [2]
    for aggregate, expected in [
        (Avg("score"), 1.5),
        (Min("score"), 1),
        (Max("score"), 2),
    ]:
        result = await AdvAuthor.objects.aggregate(db, total=aggregate)
        assert result["total"] == expected
    assert str(_resolve_expression(literal(2), resolver, context)) == ":param_1"
    assert _resolve_expression(Q(score=1), resolver, context) is not None
    with pytest.raises(TypeError):
        _resolve_expression(object(), resolver, context)


@pytest.mark.parametrize(
    "factory,error",
    [
        (lambda: When(object(), then=1), TypeError),
        (lambda: When(then=1), ValueError),
        (lambda: When(score=1, then=None), ValueError),
        (lambda: Case(), ValueError),
        (lambda: Case(1), TypeError),
    ],
)
def test_case_constructor_errors_are_explicit(factory, error):
    with pytest.raises(error):
        factory()


@pytest.mark.asyncio
async def test_case_conditions_structural_equality_and_errors(db):
    resolver = FieldResolver(QueryState(AdvAuthor))
    branch = When(Q(score__gte=1), name="Grace", then=Value(10))
    assert branch == When(Q(score__gte=1), name="Grace", then=Value(10))
    assert branch != object() and hash(branch) == hash(
        When(Q(score__gte=1), name="Grace", then=Value(10))
    )
    for expr in [F("score") + 1, F("score") ** 2, Value(2)]:
        a = When(score=1, then=expr)
        assert a == When(score=1, then=expr) and isinstance(hash(a), int)
    assert "Case(" in repr(Case(branch))
    result = (
        await AdvAuthor.objects.filter(db, name="Grace")
        .annotate(label=Case(branch, default=2))
        .values_list("label", flat=True)
    )
    assert result == [10]
    with pytest.raises(ValueError, match="zero conditions"):
        When(Q(), then=1).resolve(resolver)
    for case in [
        Case(When(score=1, then=object())),
        Case(When(score=1, then=1), default=object()),
    ]:
        with pytest.raises(FilterError):
            case.resolve(resolver)
    for case in [
        Case(When(unknown=1, then=1)),
        Case(When(score=1, then=1), default=F("unknown")),
    ]:
        with pytest.raises(FilterError):
            case.resolve(resolver)
    # Q objects that resolve empty are permitted alongside a meaningful condition.
    assert When(Q(), score=1, then=1).resolve(resolver)[0] is not None


def test_q_immutable_collections_and_logical_identity():
    for value in [
        None,
        1,
        "x",
        {"nested": [1, 2]},
        (1, [2]),
        {1, 2},
        frozenset({1, 2}),
    ]:
        first = Q(score__in=value)
        second = Q(score__in=value)
        assert first == second and hash(first) == hash(second)
    query = Q(score=1)
    assert query != object() and query != Q(score=2) and query != ~query
    assert Q() & query is query and query & Q() is query
    assert Q() | query is query and query | Q() is query
    assert bool(query) and not Q() and "NOT" in repr(~query) and "AND" in repr(query)
    for action in [lambda: Q(2), lambda: query & 2, lambda: query | 2]:
        with pytest.raises(TypeError):
            action()
    expression = F("score") + 1
    assert Q(value=expression) == Q(value=expression)
    assert Q(value=F("score") + 1) != Q(value=F("score") + 1)
    assert _freeze_value(Q(score=1))[0] == "Q"
    cycle = []
    cycle.append(cycle)
    assert _freeze_value(cycle)[1][0][0] == "cycle"
    mapping = {}
    mapping["self"] = mapping
    assert _freeze_value(mapping)[0] == "dict"
    for value in [cycle, mapping]:
        with pytest.raises(ValueError, match="Cyclic"):
            Q(value=value)
    nested = []
    tuple_cycle = (nested,)
    nested.append(tuple_cycle)
    with pytest.raises(ValueError, match="Cyclic"):
        Q(value=tuple_cycle)


def test_empty_nested_predicates_resolve_to_empty():
    resolver = FieldResolver(QueryState(AdvAuthor))
    assert Q().resolve(resolver) is None
    assert Q(Q()).resolve(resolver) is None
