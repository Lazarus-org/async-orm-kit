from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, func

from async_orm_kit.query.aggregations.base import Aggregate
from async_orm_kit.query.lookups.resolver import ResolverContext

if TYPE_CHECKING:
    from async_orm_kit.query.lookups.resolver import FieldResolver


class Min(Aggregate):
    """Find minimum value of field across grouped rows.

    Generates SQL MIN() aggregate function for finding smallest value in result set.
    Supports DISTINCT modifier (though typically redundant for MIN). Works with
    numeric, string, and date types using database-native comparison rules.

    Handles NULL values per SQL standard: NULLs are excluded from comparison, and
    MIN of all-NULL column returns NULL. For strings, uses lexicographic ordering.
    For dates, returns earliest. For numbers, returns smallest value.

    Args:
        field: Field to find minimum of. Accepts str column name, F() expression,
              Value() literal, or Case() for conditional minimum.
        distinct: Whether to consider only distinct values before finding min.
                 Typically unnecessary as MIN(field) = MIN(DISTINCT field).
                 Defaults to False.
        **extra: Additional arguments like output_field for type coercion.

    Returns:
        Minimum value of appropriate type (int, Decimal, str, date, etc.), or None
        if no non-NULL values exist.

    Note:
        Performance: MIN requires scanning all rows in group but is typically fast
        as it only tracks single minimum value. Indexed columns improve performance.

        DISTINCT redundancy: MIN(field) and MIN(DISTINCT field) produce identical
        results since minimum is same whether duplicates exist or not. DISTINCT adds
        unnecessary overhead.

        NULL handling: MIN ignores NULL values. MIN(NULL, NULL, NULL) returns NULL.
        Use COALESCE or Case() with default if you need fallback for empty groups.

        Type compatibility: Ensure compared values are same type. Mixing types may
        cause database errors or unexpected comparison behavior.
    """

    def resolve(
        self,
        field_resolver: FieldResolver,
        context: ResolverContext = ResolverContext(),
    ) -> ColumnElement:
        """Resolve to SQLAlchemy MIN() function expression.

        Args:
            field_resolver: FieldResolver for column resolution and join tracking.
            context: ResolverContext for OR context and filter marking propagation.

        Returns:
            SQLAlchemy func.min() expression with optional DISTINCT modifier.

        Example:
            >>> # Generates: MIN(orders.amount)
            >>> Min("amount").resolve(field_resolver, context)
            >>>
            >>> # Generates: MIN(DISTINCT orders.amount) - redundant but valid
            >>> Min("amount", distinct=True).resolve(field_resolver, context)
        """
        col = self._resolve_column(field_resolver=field_resolver, context=context)
        return self._finish(
            func.min(self._prepare_column(col, field_resolver, context))
        )
