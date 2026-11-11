"""Join-independent predicates for negated relationship queries.

A LEFT JOIN followed by ``IS NOT TRUE`` negates individual *joined rows*, not
an entire parent when one of its children matches. Relationship ``has``/``any``
constructs correlated EXISTS subqueries with root-identity semantics.
"""

from __future__ import annotations

from sqlalchemy import and_, inspect as sa_inspect, or_, true

from async_orm_kit.exceptions import FilterError, ModelAttributeError
from async_orm_kit.query.lookups.conditions import ConditionBuilder
from async_orm_kit.query.lookups.parser import FilterParser
from async_orm_kit.query.lookups.resolver.context import ResolverContext
from async_orm_kit.query.q import Q


def _leaf(key, value, resolver, context):
    parsed = resolver.parse_field_key(key)
    if not parsed.relationship_parts:
        return FilterParser(resolver).parse({key: value}, context=context)[0]

    current = resolver.model
    chain = []
    for part in parsed.relationship_parts:
        relation = sa_inspect(current).relationships.get(part)
        if relation is None:
            raise ModelAttributeError(
                f"Invalid relationship {part!r} on {current.__name__}"
            )
        chain.append(getattr(current, part))
        current = relation.mapper.class_

    if parsed.field_name:
        if parsed.field_name == "pk":
            pk = sa_inspect(current).primary_key
            if len(pk) != 1:
                raise ModelAttributeError("Composite PK requires an explicit field")
            attribute = getattr(current, pk[0].key)
        else:
            mapper = sa_inspect(current)
            if parsed.field_name not in mapper.column_attrs:
                raise ModelAttributeError(
                    f"{current.__name__} has no mapped column {parsed.field_name!r}"
                )
            attribute = getattr(current, parsed.field_name)
        predicate = ConditionBuilder(resolver).build(
            attribute, parsed.lookup, value, context=context
        )
    else:
        # Existence-only relation checks (e.g. exclude(posts__isnull=False))
        if value is None:
            predicate = true()
        else:
            raise FilterError(
                "Negated relationship predicates require a terminal field; "
                "use relation__pk or an explicit isnull lookup"
            )

    for relationship in reversed(chain):
        predicate = (
            relationship.any(predicate)
            if relationship.property.uselist
            else relationship.has(predicate)
        )
    return predicate


def resolve_q(q: Q, resolver, context: ResolverContext):
    """Resolve Q with correlated EXISTS for paths involving relationships."""
    if not q.children:
        return None
    expressions = []
    for child in q.children:
        if isinstance(child, Q):
            expr = resolve_q(child, resolver, context)
            if expr is not None:
                expressions.append(expr)
        else:
            _, key, value = child
            expressions.append(_leaf(key, value, resolver, context))
    if not expressions:
        return None
    result = or_(*expressions) if q.connector == Q.OR else and_(*expressions)
    return result.is_not(true()) if q.negated else result
