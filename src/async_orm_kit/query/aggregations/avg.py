from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, func

from async_orm_kit.query.aggregations.base import Aggregate
from async_orm_kit.query.lookups.resolver import ResolverContext

if TYPE_CHECKING:
    from async_orm_kit.query.lookups.resolver import FieldResolver


class Avg(Aggregate):
    """Calculate average (mean) of numeric field values across grouped rows.

    Generates SQL AVG() aggregate function for computing arithmetic mean of field
    values. Supports DISTINCT modifier to average only unique values. Commonly used
    with QuerySet.annotate() for per-group averages or QuerySet.aggregate() for
    overall dataset average.

    Handles NULL values per SQL standard: NULLs are excluded from calculation, and
    AVG of all-NULL column returns NULL. Works with numeric types (integers, decimals,
    floats) and may return decimal results even for integer inputs depending on
    database dialect.

    Args:
        field: Field to average. Accepts str column name, F() expression for computed
              averages, Value() for literals, or Case() for conditional averaging.
        distinct: Whether to average only distinct values. Useful for deduplicating
                 before averaging. Defaults to False.
        **extra: Additional arguments like output_field for type coercion.

    Returns:Decimal or float representing average value, or None if no non-NULL values.

    Note:
        Performance: AVG requires scanning all rows in group. On large datasets,
        ensure grouped columns are indexed. DISTINCT adds deduplication overhead.

        Database differences: PostgreSQL returns numeric type, MySQL returns double.
        Use output_field parameter for consistent type handling across databases.

        NULL handling: AVG(NULL, NULL, NULL) returns NULL, not 0. Use COALESCE or
        Case() with default=Value(0) if you need zero for empty groups.
    """

    def resolve(
        self,
        field_resolver: FieldResolver,
        context: ResolverContext = ResolverContext(),
    ) -> ColumnElement:
        """Resolve to SQLAlchemy AVG() function expression.

        Args:
            field_resolver: FieldResolver for column resolution and join tracking.
            context: ResolverContext for OR context and filter marking propagation.

        Returns:
            SQLAlchemy func.avg() expression with optional DISTINCT modifier.

        Example:
            >>> # Generates: AVG(orders.amount)
            >>> Avg("amount").resolve(field_resolver, context)
            >>>
            >>> # Generates: AVG(DISTINCT orders.amount)
            >>> Avg("amount", distinct=True).resolve(field_resolver, context)
        """
        col = self._resolve_column(field_resolver=field_resolver, context=context)
        return self._finish(
            func.avg(self._prepare_column(col, field_resolver, context))
        )
