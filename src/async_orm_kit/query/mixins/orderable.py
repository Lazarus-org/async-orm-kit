from typing import Any

from async_orm_kit.query.lookups.resolver import ResolverContext


class OrderedExpression:
    """Wrapper that adds sort direction to ORM expressions for use in ``order_by()``.

    Encapsulates any ORM expression (field reference, computed expression, aggregate,
    or conditional) with an ascending or descending sort direction. Created by
    calling ``.asc()`` or ``.desc()`` on any :class:`OrderableMixin` subclass.

    During query compilation, the wrapped expression is resolved to a SQLAlchemy
    column/expression, then the appropriate ``.asc()`` or ``.desc()`` method is
    applied to generate the final ORDER BY clause element.

    Args:
        expression: Any ORM expression with a ``resolve(field_resolver, context)``
            method. Typically a :class:`F`, :class:`FExpression`, :class:`Aggregate`,
            or :class:`Case` instance.
        ascending: If ``True``, sort in ascending order (smallest to largest).
            If ``False``, sort in descending order (largest to smallest).Defaults to ``True``.Attributes:
        expression: The wrapped ORM expression to be sorted.
        ascending (bool): Sort direction flag.

    Example:
        >>> from async_orm_kit.query.expressions.f import F
        >>> from async_orm_kit.query.aggregations import Sum
        >>> from async_orm_kit.query.expressions.case import Case, When
        >>> from async_orm_kit.query.expressions.value import Value
        >>>
        >>> # Simple field ordering
        >>> Product.objects.order_by(F("price").desc())
        >>> # SQL: ORDER BY "price" DESC
        >>>
        >>> # Computed expression ordering
        >>> Product.objects.order_by((F("price") * F("quantity")).asc())
        >>> # SQL: ORDER BY ("price" * "quantity") ASC
        >>>
        >>> # Aggregate ordering (requires annotation)
        >>> Product.objects.annotate(
        ...     total_sales=Sum("order_items__quantity")
        ... ).order_by(F("total_sales").desc())
        >>> # SQL: ORDER BY SUM("order_items"."quantity") DESC
        >>>
        >>> # Conditional ordering
        >>> priority = Case(
        ...     When(status="urgent", then=Value(0)),
        ...     When(status="high", then=Value(1)),
        ...     default=Value(2)
        ... )
        >>> Task.objects.order_by(priority.asc(), F("created_at").desc())
        >>> # SQL: ORDER BY CASE ... END ASC, "created_at" DESC

    Note:
        - The wrapped expression is **not resolved** until query compilation time,
          allowing the same ``OrderedExpression`` instance to be reused across
          multiple queries.
        - Multiple ``OrderedExpression`` instances can be passed to ``order_by()``
          to create multi-column sorts with mixed directions.
        - Database NULL handling varies by backend and can be controlled with
          ``NULLS FIRST`` / ``NULLS LAST`` (not currently supported by this class,
          but available via raw SQLAlchemy if needed).

    See Also:
        - :class:`OrderableMixin` for the ``.asc()`` / ``.desc()`` factory methods.
        - :meth:`QuerySet.order_by` for usage in query construction.
    """

    def __init__(self, expression: Any, ascending: bool = True):
        self.expression = expression
        self.ascending = ascending

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        """Resolve the wrapped expression and apply sort direction.Delegates resolution to the wrapped expression's ``resolve()`` method,
        then applies SQLAlchemy's ``.asc()`` or ``.desc()`` method to the
        resulting column/expression to produce the final ORDER BY element.

        Args:
            field_resolver: FieldResolver instance for resolving field paths
                and managing JOIN registration.
            context: Resolution context for JOIN optimization. Defaults to
                empty context (no special flags).

        Returns:
            SQLAlchemy column expression with ``.asc()`` or ``.desc()`` applied,
            ready for inclusion in an ORDER BY clause.

        Example:
            >>> resolver = FieldResolver(Product, join_graph)
            >>> ordered = F("price").desc()
            >>> sql_expr = ordered.resolve(resolver)
            >>> # Returns: Product.price.desc() (SQLAlchemy UnaryExpression)

        Note:
            The ``context`` parameter is propagated to the wrapped expression's
            resolution, ensuring consistent JOIN strategy if the expression
            references relationships.
        """
        resolved = self.expression.resolve(field_resolver, context)
        if self.ascending:
            return resolved.asc()
        return resolved.desc()

    def __repr__(self) -> str:
        """Return developer-friendly string representation.

        Returns:
            String showing the wrapped expression and sort direction.

        Example:
            >>> repr(F("price").desc())
            >>> # Returns: "OrderedExpression(F('price'), DESC)"
        """
        direction = "ASC" if self.ascending else "DESC"
        return f"OrderedExpression({self.expression!r}, {direction})"


class OrderableMixin:
    """Mixin that adds ``.asc()`` and ``.desc()`` methods for sort direction control.

    Enables any ORM expression class to produce :class:`OrderedExpression` instances
    for use in ``order_by()`` clauses. Classes inheriting this mixin can be sorted
    in ascending or descending order by calling the provided methods.

    Classes Using This Mixin:
        - :class:`F` — field references (``F("created_at").desc()``)
        - :class:`FExpression` — computed expressions (``(F("a") + F("b")).asc()``)
        - :class:`Aggregate` — aggregate results (``Sum("amount").desc()``)
        - :class:`Case` — conditional expressions (``Case(...).asc()``)
        - :class:`Value` — literal values (``Value(1).desc()`` — rare but valid)

    Example:
        >>> from async_orm_kit.query.expressions.f import F
        >>> from async_orm_kit.query.aggregations import Count
        >>> # Ascending sort (default direction)
        >>> Product.objects.order_by(F("name").asc())
        >>> # SQL: ORDER BY "name" ASC
        >>>
        >>> # Descending sort
        >>> Product.objects.order_by(F("price").desc())
        >>> # SQL: ORDER BY "price" DESC
        >>>
        >>> # Multi-column sort with mixed directions
        >>> Product.objects.order_by(
        ...     F("category").asc(),
        ...     F("price").desc(),
        ...     F("name").asc()
        ... )
        >>> # SQL: ORDER BY "category" ASC, "price" DESC, "name" ASC
        >>>
        >>> # Sorting by computed expression
        >>> Product.objects.order_by((F("price") * F("quantity")).desc())
        >>> # SQL: ORDER BY ("price" * "quantity") DESC
        >>>
        >>> # Sorting by aggregate (requires annotation)
        >>> Category.objects.annotate(
        ...     product_count=Count("products")
        ... ).order_by(F("product_count").desc())
        >>> # SQL: ORDER BY COUNT("products"."id") DESC

    Note:
        - Both methods return new :class:`OrderedExpression` instances; the
          original expression object is **never mutated** (immutable pattern).
        - The ``__slots__ = ()`` declaration prevents instance attribute creation,
          ensuring this mixin adds no memory overhead to inheriting classes.
        - Calling ``.asc()`` or ``.desc()`` multiple times on the same expression
          creates independent ``OrderedExpression`` wrappers — the last one used
          in ``order_by()`` determines the actual sort direction.

    See Also:
        - :class:`OrderedExpression` for the wrapper class structure.
        - :meth:`QuerySet.order_by` for usage in query construction.
    """

    __slots__ = ()

    def asc(self) -> OrderedExpression:
        """Create an ascending sort wrapper for this expression.

        Returns:
            OrderedExpression with ``ascending=True``, suitable for passing
            to ``order_by()``.

        Example:
            >>> F("price").asc()
            >>> # Returns: OrderedExpression(F('price'), ASC)
            >>>
            >>> Product.objects.order_by(F("created_at").asc())
            >>> # SQL: ORDER BY "created_at" ASC
        """
        return OrderedExpression(self, True)

    def desc(self) -> OrderedExpression:
        """Create a descending sort wrapper for this expression.

        Returns:
            OrderedExpression with ``ascending=False``, suitable for passing
            to ``order_by()``.

        Example:
            >>> F("price").desc()
            >>> # Returns: OrderedExpression(F('price'), DESC)
            >>>
            >>> Product.objects.order_by(F("price").desc())
            >>> # SQL: ORDER BY "price" DESC
        """
        return OrderedExpression(self, False)
