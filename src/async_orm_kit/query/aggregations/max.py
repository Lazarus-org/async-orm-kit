from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, func

from async_orm_kit.query.aggregations.base import Aggregate
from async_orm_kit.query.lookups.resolver import ResolverContext

if TYPE_CHECKING:
    from async_orm_kit.query.lookups.resolver import FieldResolver


class Max(Aggregate):
    """Find maximum value of field across grouped rows.

    Generates SQL MAX() aggregate function for finding largest value in result set.
    Supports DISTINCT modifier (though typically redundant for MAX). Works with
    numeric, string, and date types using database-native comparison rules.

    Handles NULL values per SQL standard: NULLs are excluded from comparison, and
    MAX of all-NULL column returns NULL. For strings, uses lexicographic ordering.
    For dates, returns most recent. For numbers, returns largest value.

    Args:
        field: Field to find maximum of. Accepts str column name, F() expression,
              Value() literal, or Case() for conditional maximum.
        distinct: Whether to consider only distinct values before finding max.
                 Typically unnecessary as MAX(field) = MAX(DISTINCT field).
                 Defaults to False.
        **extra: Additional arguments like output_field for type coercion.

    Returns:
        Maximum value of appropriate type (int, Decimal, str, date, etc.), or None
        if no non-NULL values exist.

    Note:
        Performance: MAX requires scanning all rows in group but is typically fast
        as it only tracks single maximum value. Indexed columns improve performance.

        DISTINCT redundancy: MAX(field) and MAX(DISTINCT field) produce identical
        results since maximum is same whether duplicates exist or not. DISTINCT adds
        unnecessary overhead.

        NULL handling: MAX ignores NULL values. MAX(NULL, NULL, NULL) returns NULL.
        Use COALESCE or Case() with default if you need fallback for empty groups.

        Type compatibility: Ensure compared values are same type. Mixing types may
        cause database errors or unexpected comparison behavior.
    """

    def resolve(
        self,
        field_resolver: FieldResolver,
        context: ResolverContext = ResolverContext(),
    ) -> ColumnElement:
        """Resolve to SQLAlchemy MAX() function expression.

        Args:
            field_resolver: FieldResolver for column resolution and join tracking.
            context: ResolverContext for OR context and filter marking propagation.

        Returns:
            SQLAlchemy func.max() expression with optional DISTINCT modifier.

        Example:
            >>> # Generates: MAX(orders.amount)
            >>> Max("amount").resolve(field_resolver, context)
            >>>
            >>> # Generates: MAX(DISTINCT orders.amount) - redundant but valid
            >>> Max("amount", distinct=True).resolve(field_resolver, context)
        """
        col = self._resolve_column(field_resolver=field_resolver, context=context)
        return self._finish(
            func.max(self._prepare_column(col, field_resolver, context))
        )
