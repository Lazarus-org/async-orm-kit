from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, func

from async_orm_kit.query.aggregations.base import Aggregate
from async_orm_kit.query.lookups.resolver import ResolverContext

if TYPE_CHECKING:
    from async_orm_kit.query.lookups.resolver import FieldResolver


class Sum(Aggregate):
    """Calculate sum (total) of numeric field values across grouped rows.

    Generates SQL SUM() aggregate function for computing arithmetic sum of field
    values. Supports DISTINCT modifier to sum only unique values. Commonly used
    for totaling amounts, quantities, or other numeric metrics.

    Handles NULL values per SQL standard: NULLs are excluded from calculation, and
    SUM of all-NULL column returns NULL (not 0). Works with numeric types (integers,
    decimals, floats) and preserves precision for decimal types.

    Args:
        field: Field to sum. Accepts str column name, F() expression for computed
              sums, Value() for literals, or Case() for conditional summation.
        distinct: Whether to sum only distinct values. Useful for deduplicating
                 before summing. Defaults to False.
        **extra: Additional arguments like output_field for type coercion.

    Returns:
        Numeric value (int, Decimal, or float) representing sum, or None if no
        non-NULL values exist.

    Note:
        Performance: SUM requires scanning all rows in group. On large datasets,
        ensure grouped columns are indexed. DISTINCT adds deduplication overhead.

        NULL handling: SUM(NULL, NULL, NULL) returns NULL, not 0. Use COALESCE or
        Case() with default=Value(0) if you need zero for empty groups:
        Sum(Case(When(condition, then=F("amount")), default=Value(0)))
        Precision: For decimal fields, SUM preserves precision. For integer fields,
        result may overflow on very large sums depending on database dialect.
        DISTINCT usage: SUM(DISTINCT amount) sums unique amounts only, useful when
        dealing with denormalized data where same value appears multiple times.
    """

    def resolve(
        self,
        field_resolver: FieldResolver,
        context: ResolverContext = ResolverContext(),
    ) -> ColumnElement:
        """Resolve to SQLAlchemy SUM() function expression.

        Args:
            field_resolver: FieldResolver for column resolution and join tracking.
            context: ResolverContext for OR context and filter marking propagation.

        Returns:
            SQLAlchemy func.sum() expression with optional DISTINCT modifier.

        Example:
            >>> # Generates: SUM(orders.amount)
            >>> Sum("amount").resolve(field_resolver, context)
            >>>
            >>> # Generates: SUM(DISTINCT orders.amount)
            >>> Sum("amount", distinct=True).resolve(field_resolver, context)
        """
        col = self._resolve_column(field_resolver=field_resolver, context=context)
        return self._finish(
            func.sum(self._prepare_column(col, field_resolver, context))
        )
