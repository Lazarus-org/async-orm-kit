from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement

from async_orm_kit.query.lookups.resolver import ResolverContext
from async_orm_kit.query.mixins.combinable import CombinableMixin
from async_orm_kit.query.mixins.orderable import OrderableMixin

if TYPE_CHECKING:
    from async_orm_kit.query.lookups.resolver import FieldResolver


class Aggregate(CombinableMixin, OrderableMixin):
    """Base class for aggregate functions like Count, Sum, Avg, Min, Max.Provides common infrastructure for SQL aggregate functions that operate on
    groups of rows to produce single values. Supports field resolution from strings,
    F() expressions, Value() literals, and Case() conditionals. Handles DISTINCT
    modifier for unique value aggregation. Subclasses implement specific aggregate
    functions by overriding resolve() to generate appropriate SQLAlchemy expressions.
    Aggregates are typically used with QuerySet.annotate() to add computed fields
    to query results, often combined with GROUP BY through relationship traversal
    or explicit grouping. They can also be used in QuerySet.aggregate() for
    single-row summary statistics.

    Attributes:
        field: Field specification to aggregate - can be column name string, F()
              expression for field references, Value() for literals, or Case() for
              conditional aggregation.
        distinct: Whether to apply DISTINCT modifier to aggregate only unique values.
        extra: Additional keyword arguments for subclass-specific configuration.

    Note:
        Performance: Aggregates trigger GROUP BY clauses which can be expensive on
        large datasets. Ensure grouped columns are indexed. DISTINCT aggregates are
        slower than non-distinct as they require deduplication.
        This is an abstract base class - use concrete subclasses like Count, Sum,
        Avg, Min, Max for actual aggregation operations. Subclasses must implement
        resolve() to generate SQLAlchemy aggregate expressions.
    """

    def __init__(self, field: Any, distinct: bool = False, **extra):
        """Initialize aggregate with field specification and options.

        Args:
            field: Field to aggregate. Accepts multiple types:
                  - str: Column name like "amount" or relationship path like "orders__amount"
                  - F(): Field expression for computed aggregation like F("quantity") * F("price")
                  - Value(): Literal value for conditional aggregation like Value(1)
                  - Case(): Conditional expression for filtered aggregationdistinct: Whether to aggregate only distinct values. Useful for countingunique values like Count("customer_id", distinct=True). Defaults to False.
            **extra: Additional keyword arguments passed to subclass for specific
                    configuration (e.g., output_field for type coercion).

        Example:
            >>> # String field name
            >>> agg = Sum("amount")
            >>>
            >>> # DISTINCT modifier
            >>> agg = Count("customer_id", distinct=True)
            >>>
            >>> # F() expression
            >>> agg = Sum(F("quantity") * F("unit_price"))
            >>>
            >>> # Case() conditional
            >>> agg = Sum(Case(When(status="completed", then=Value(1)), default=Value(0)))
            >>>
            >>> # Extra arguments for subclass
            >>> agg = Avg("rating", output_field=DecimalField())
        """
        unknown = set(extra) - {"filter", "default", "output_field"}
        if unknown:
            raise TypeError(f"Unsupported aggregate options: {sorted(unknown)}")
        if "filter" in extra:
            from async_orm_kit.query.q import Q

            if not isinstance(extra["filter"], Q):
                raise TypeError("Aggregate filter must be a Q expression")
        self.field = field
        self.distinct = distinct
        self.extra = extra

    def _prepare_column(self, col, field_resolver, context):
        """Apply a portable CASE expression for conditional aggregation."""
        predicate = self.extra.get("filter")
        if predicate is not None:
            from sqlalchemy import case

            resolved = predicate.resolve(field_resolver=field_resolver, context=context)
            col = case((resolved, col), else_=None)
        return self._maybe_distinct(col)

    def _finish(self, aggregate):
        """Apply optional SQL result type and empty-group default."""
        from sqlalchemy import cast, func
        from sqlalchemy.types import TypeEngine

        output = self.extra.get("output_field")
        if output is not None:
            if isinstance(output, type) and issubclass(output, TypeEngine):
                output = output()
            if not isinstance(output, TypeEngine):
                raise TypeError("output_field must be a SQLAlchemy TypeEngine")
            aggregate = cast(aggregate, output)
        if "default" in self.extra:
            aggregate = func.coalesce(aggregate, self.extra["default"])  # pylint: disable=assignment-from-no-return  # SQLAlchemy constructs a SQL expression.
        return aggregate

    def _maybe_distinct(self, col):
        """Apply DISTINCT modifier to column if requested.Wraps SQLAlchemy column expression with .distinct() method when self.distinct
        is True. Used by subclasses in resolve() to generate DISTINCT aggregates like
        COUNT(DISTINCT customer_id) or SUM(DISTINCT amount).

        Args:
            col: SQLAlchemy column or expression to potentially wrap with DISTINCT.

        Returns:
            Column with .distinct() applied if self.distinct is True, otherwise
            returns column unchanged.

        Note:
            Performance: DISTINCT requires deduplication which can be expensive on
            large result sets. Database must sort or hash values to identify duplicates.
            Use only when necessary for correctness.
        """
        return col.distinct() if self.distinct else col

    def _resolve_column(
        self, field_resolver: FieldResolver, context: ResolverContext
    ) -> ColumnElement:
        """Resolve self.field to SQLAlchemy column or expression for aggregation.

        Handles multiple field types with priority-based resolution:
        1. Case() - Delegates to Case.resolve() for conditional expressions
        2. Value() - Delegates to Value.resolve() for literal wrapping
        3. F() / FExpression() - Delegates to F.resolve() for field references with auto-join
        4. str - Plain field name resolved via FieldResolver for column lookup and auto-join

        Uses shared FieldResolver to maintain join state across entire query, ensuring
        relationship traversal in aggregates (e.g., "orders__amount") properly registers
        joins with QueryBuilder. Context propagation enables correct join strategy
        (INNER vs LEFT) based on filter/annotation usage.

        Args:
            field_resolver: FieldResolver instance from QueryBuilder maintaining join
                          state and column resolution. Must be provided in production
                          code paths for proper join tracking.
            context: ResolverContext tracking OR context for join strategy and filter
                    marking for INNER join promotion. Propagated to nested expressions.

        Returns:
            SQLAlchemy ColumnElement representing resolved field for aggregation.

        Raises:
            TypeError: If field is not str, F(), Value(), or Case() - unsupported type
                      for aggregation.

        Example:
            >>> # Internal usage in subclass resolve()
            >>> from async_orm_kit.query.aggregations import Sum
            >>>
            >>> # String field resolution
            >>> agg = Sum("amount")
            >>> col = agg._resolve_column(field_resolver, context)
            >>> # Returns: Order.amount column
            >>>
            >>> # F() expression resolution
            >>> agg = Sum(F("quantity") * F("unit_price"))
            >>> col = agg._resolve_column(field_resolver, context)
            >>> # Returns: Order.quantity * Order.unit_price expression
            >>>
            >>> # Case() conditional resolution
            >>> agg = Sum(Case(When(status="completed", then=Value(1)), default=Value(0)))
            >>> col = agg._resolve_column(field_resolver, context)
            >>> # Returns: CASE WHEN status='completed' THEN 1 ELSE 0 END expression
            >>>
            >>> # Relationship traversal with auto-join
            >>> agg = Sum("orders__amount")
            >>> col = agg._resolve_column(field_resolver, context)
            >>> # Returns: Order.amount column with JOIN registered in field_resolver

        Note:
            Performance: Resolution is O(1) for simple fields, O(n) for relationship
            paths where n is traversal depth. F() expressions may resolve multiple
            fields. Case() expressions resolve all When conditions.
            Join tracking: All relationship traversal registers joins with field_resolver,
            ensuring QueryBuilder generates proper JOIN clauses. Context propagation
            ensures correct join strategy based on usage (filter vs annotation, OR context).
        """
        # Structured expressions
        if isinstance(self.field, CombinableMixin) and hasattr(self.field, "resolve"):
            return self.field.resolve(field_resolver=field_resolver, context=context)

        # Plain string field name
        if isinstance(self.field, str):
            # A relationship is not a scalar SQL column. Resolving a bare
            # relation through SQLAlchemy normally produces a boolean join
            # comparison; COUNT(relation) then counts the wrong rows and can
            # silently introduce a Cartesian product.
            from sqlalchemy import inspect as sa_inspect

            parts = self.field.split("__")
            current = field_resolver.model
            terminal_relation = None
            for part in parts:
                inspected: Any = sa_inspect(current)
                mapper = inspected.mapper if inspected.is_aliased_class else inspected
                relation = mapper.relationships.get(part)
                if relation is None:
                    terminal_relation = None
                    break
                terminal_relation = relation
                current = relation.mapper.class_
            if terminal_relation is not None:
                primary_key: Any = sa_inspect(current).primary_key
                if len(primary_key) != 1:
                    raise ValueError(
                        "Aggregate over a composite-key relationship requires "
                        "an explicit related column"
                    )
                key = f"{self.field}__{primary_key[0].key}"
            else:
                key = self.field
            resolution = field_resolver.resolve_to_column(key=key, context=context)
            return resolution.column

        raise TypeError(
            f"Aggregate field must be a str, F(), Value(), or Case(). "
            f"Got: {type(self.field).__name__}"
        )

    def resolve(
        self,
        field_resolver: FieldResolver,
        context: ResolverContext = ResolverContext(),
    ) -> ColumnElement:
        """Resolve aggregate to SQLAlchemy expression for query compilation.

        Abstract method that subclasses must implement to generate specific aggregate
        functions like COUNT(), SUM(), AVG(), MIN(), MAX(). Should use _resolve_column()
        to handle field resolution and _maybe_distinct() to apply DISTINCT modifier.

        Args:
            field_resolver: FieldResolver instance for column resolution and join tracking.
            context: ResolverContext for OR context and filter marking propagation.

        Returns:
            SQLAlchemy ColumnElement representing aggregate function expression.

        Raises:
            NotImplementedError: Always raised - subclasses must override this method.

        Example:
            >>> # Subclass implementation example
            >>> class Sum(Aggregate):
            ...     def resolve(self, field_resolver, context=ResolverContext()):
            ...         col = self._resolve_column(field_resolver, context)
            ...         col = self._maybe_distinct(col)
            ...         return func.sum(col)

        Note:
            Subclasses: Count, Sum, Avg, Min, Max all implement this method to generate
            their respective SQL aggregate functions using SQLAlchemy's func namespace.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        """Return string representation showing aggregate type, field, and options.

        Returns:
            String in format "AggregateClass(field, distinct=True)" for debugging.

        Example:
            >>> Sum("amount")
            "Sum('amount')"
            >>>
            >>> Count("customer_id", distinct=True)
            "Count('customer_id', distinct=True)"
            >>>
            >>> Avg(F("quantity") * F("price"))
            "Avg(F('quantity') * F('price'))"
        """
        parts = [repr(self.field)]
        if self.distinct:
            parts.append("distinct=True")
        return f"{self.__class__.__name__}({', '.join(parts)})"


class AggregateFunction(Aggregate):
    """Custom aggregate backed by a trusted SQLAlchemy expression factory.

    ``factory(resolved_column)`` must return a SQLAlchemy ColumnElement. This
    allows dialect-specific aggregate functions without a global SQL namespace
    registry, preserving bound values and expression-context analysis.

    Example::
        AggregateFunction("score", lambda col: func.group_concat(col))
    """

    def __init__(self, field: Any, factory: Any, distinct: bool = False, **extra):
        if not callable(factory):
            raise TypeError("AggregateFunction factory must be callable")
        super().__init__(field, distinct=distinct, **extra)
        self.factory = factory

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        from sqlalchemy.sql.elements import ColumnElement

        column = self._resolve_column(field_resolver, context)
        result = self.factory(self._prepare_column(column, field_resolver, context))
        if not isinstance(result, ColumnElement):
            raise TypeError(
                "AggregateFunction factory must return a SQLAlchemy ColumnElement"
            )
        # A custom dialect aggregate may not be named in the built-in function
        # analyzer. Mark it explicitly so nested arithmetic and CASE grouping
        # logic see the aggregate without parsing arbitrary SQL text.
        return self._finish(result._annotate({"async_orm_kit_aggregate": True}))
