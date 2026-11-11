from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, func

from async_orm_kit.query.aggregations.base import Aggregate
from async_orm_kit.query.lookups.resolver import ResolverContext

if TYPE_CHECKING:
    from async_orm_kit.query.lookups.resolver import FieldResolver


class Count(Aggregate):
    """Count number of rows or non-NULL values in grouped result set.

    Generates SQL COUNT() aggregate function for counting rows or field values.
    Supports special "*" syntax for counting all rows regardless of NULL values,
    and DISTINCT modifier for counting unique values. Most commonly used aggregate
    for result set sizing and cardinality checks.

    ``COUNT(*)`` counts all rows including those with NULL values in counted field.
    COUNT(field) counts only rows where field is non-NULL. COUNT(DISTINCT field)
    counts unique non-NULL values.

    Args:
        field: Field to count. Accepts "*" for row count (default), str column name,
              F() expression, Value() literal, or Case() conditional. Defaults to "*".
        distinct: Whether to count only distinct values. Cannot be used with "*".
                 Defaults to False.

    Returns:
        ColumnElement: SQL expression counting rows or non-NULL values.

    Raises:
        ValueError: If distinct=True with field="*" (invalid SQL syntax).

    Note:
        Performance: ``COUNT(*)`` is typically fastest as it doesn't check NULL values.
        COUNT(field) requires NULL checking. COUNT(DISTINCT field) adds deduplication
        overhead and may require sorting or hashing.

        NULL handling: COUNT(field) excludes NULL values. Use ``COUNT(*)`` to include
        rows with NULL in the counted field.
        DISTINCT validation: ``COUNT(DISTINCT *)`` is invalid SQL and raises ValueError
        at resolution time to prevent database errors.
    """

    def __init__(self, field: Any = "*", distinct: bool = False, **extra) -> None:
        """Initialize Count aggregate with field and DISTINCT option.

        Args:
            field: Field to count. Use "*" for all rows (default), or specify column
                  name, F() expression, Value(), or Case() for conditional counting.
            distinct: Whether to count only unique values. Cannot be True when field="*".

        Example:
            >>> # Count all rows
            >>> Count()
            >>> Count("*")
            >>>
            >>> # Count non-NULL values
            >>> Count("customer_id")
            >>>
            >>> # Count unique values
            >>> Count("customer_id", distinct=True)
            >>>
            >>> # Conditional count
            >>> Count(Case(When(status="active", then=Value(1))))"""
        super().__init__(field, distinct=distinct, **extra)

    def resolve(
        self,
        field_resolver: FieldResolver,
        context: ResolverContext = ResolverContext(),
    ) -> ColumnElement:
        """Resolve to SQLAlchemy COUNT() function expression.
        Handles special "*" syntax for row counting and validates DISTINCT usage.
        For non-"*" fields, delegates to _resolve_column() for field resolution.

        Args:
            field_resolver: FieldResolver for column resolution and join tracking.
            context: ResolverContext for OR context and filter marking propagation.

        Returns:
            SQLAlchemy func.count() expression with optional DISTINCT modifier.

        Raises:
            ValueError: If distinct=True with field="*" (``COUNT(DISTINCT *)`` is invalid SQL).

        Example:
            >>> # Generates: ``COUNT(*)``
            >>> Count().resolve(field_resolver, context)
            >>>
            >>> # Generates: COUNT(orders.customer_id)
            >>> Count("customer_id").resolve(field_resolver, context)
            >>>
            >>> # Generates: COUNT(DISTINCT orders.customer_id)
            >>> Count("customer_id", distinct=True).resolve(field_resolver, context)>>>
            >>> # Raises ValueError
            >>> Count("*", distinct=True).resolve(field_resolver, context)
        """
        col: Any
        if isinstance(self.field, str) and self.field == "*":
            if self.distinct:
                raise ValueError("``COUNT(DISTINCT *)`` is not valid SQL.")
            if self.extra.get("filter") is None:
                return self._finish(func.count())
            from sqlalchemy import literal

            col = literal(1)
        else:
            col = self._resolve_column(field_resolver=field_resolver, context=context)
        col = self._prepare_column(col, field_resolver, context)
        return self._finish(func.count(col))
