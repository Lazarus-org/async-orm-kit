from __future__ import annotations

import operator
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from async_orm_kit.query.expressions.f import FExpression


class CombinableMixin:
    """Mixin that adds arithmetic operators to ORM expression classes.

    Enables any ORM expression with a ``resolve(field_resolver)`` method to
    participate in database-level arithmetic through Python's operator
    overloading. All operations produce :class:`FExpression` instances that
    defer actual SQL generation until query compilation time.

    This mixin implements both forward (``self OP other``) and reflected
    (``other OP self``) variants for all binary operators, ensuring correct
    behavior regardless of operand order. Unary operators (negation, absolute
    value) are also supported.

    Supported Operations:
        - **Addition**: ``expr + other``, ``other + expr``
        - **Subtraction**: ``expr - other``, ``other - expr``
        - **Multiplication**: ``expr * other``, ``other * expr``
        - **Division**: ``expr / other``, ``other / expr``
        - **Modulo**: ``expr % other``, ``other % expr``
        - **Negation**: ``-expr``
        - **Absolute Value**: ``abs(expr)``

    Classes Using This Mixin:
        - :class:`F` — field references (``F("price") * 1.1``)
        - :class:`Value` — literal values (``Value(100) - F("discount")``)
        - :class:`Aggregate` — aggregate results (``Sum("qty") * F("price")``)
        - :class:`FExpression` — nested expressions (composable trees)

    Example:
        >>> from async_orm_kit.query.expressions import F, Value
        >>>
        >>> # Simple field arithmetic — produces FExpression tree
        >>> discount_price = F("price") * Value(0.9)
        >>>
        >>> # Mixed operand types — reflected operators handle int/float LHS
        >>> adjusted = 100 - F("discount")  # calls F.__rsub__, wraps 100 as LHS
        >>>
        >>> # Deep composition — arbitrary nesting
        >>> margin = (F("revenue") - F("cost")) / F("revenue") * Value(100)
        >>>
        >>> # Unary operators
        >>> negated = -F("balance")          # FExpression(None, operator.neg, F("balance"))
        >>> absolute = abs(F("balance"))     # FExpression(None, "abs", F("balance"))

    Note:
        - All operators return new :class:`FExpression` instances; the original
          expression objects are **never mutated** (immutable pattern).
        - The ``other`` operand can be any type: another ``CombinableMixin``
          subclass, a raw Python scalar (int, float, Decimal), or any object.
          Non-expression operands are wrapped in :class:`Value` during
          resolution by :class:`FExpression`.
        - Reflected operators (``__radd__``, ``__rsub__``, etc.) are invoked by
          Python when the left operand doesn't support the operation, enabling
          expressions like ``2 * F("price")`` where ``int.__mul__`` returns
          ``NotImplemented``.

    See Also:
        - :class:`FExpression` for the expression tree node structure and
          resolution logic.
        - :class:`F` for field reference construction.
        - :class:`Value` for literal value wrapping.
    """

    def _make_expression(
        self,
        op: Callable,
        other: Any,
        reverse: bool = False,
    ) -> FExpression:
        """Create a binary :class:`FExpression` node from this expression and another operand.

        Factory method used by all arithmetic dunder methods to construct
        expression tree nodes. Handles operand ordering for both forward
        and reflected operations.

        Args:
            op: Python operator function from the :mod:`operator` module
                (e.g., ``operator.add``, ``operator.mul``). Stored in the
                resulting :class:`FExpression` and applied during resolution.
            other: Right-hand operand for forward operations, or left-hand
                operand for reflected operations. Can be any type — non-expression
                values are wrapped in :class:`Value` during FExpression resolution.
            reverse: If ``True``, places ``other`` as the left operand and
                ``self`` as the right operand. Used by reflected operators
                (``__radd__``, ``__rsub__``, etc.) to preserve mathematical
                correctness for non-commutative operations.

        Returns:
            FExpression: New expression tree node representing ``self OP other``
            (forward) or ``other OP self`` (reflected).

        Example:
            >>> expr = F("price")
            >>> # Forward: F("price") + 10 → FExpression(F("price"), add, 10)
            >>> result = expr._make_expression(operator.add, 10)
            >>>
            >>> # Reflected: 10 - F("price") → FExpression(10, sub, F("price"))
            >>> result = expr._make_expression(operator.sub, 10, reverse=True)

        Note:
            Uses a deferred import for :class:`FExpression` to avoid circular
            dependencies, since FExpression itself inherits CombinableMixin.
        """
        from async_orm_kit.query.expressions.f import FExpression

        if reverse:
            return FExpression(other, op, self)
        return FExpression(self, op, other)

    def __add__(self, other: Any) -> FExpression:
        """Create addition expression: ``self + other``.

        Args:
            other: Right-hand operand. Can be another expression or a scalar.

        Returns:
            FExpression representing ``self + other``.

        Example:
            >>> F("base_price") + F("tax")
            >>> F("score") + 10
        """
        return self._make_expression(operator.add, other)

    def __sub__(self, other: Any) -> FExpression:
        """Create subtraction expression: ``self - other``.

        Args:
            other: Right-hand operand (subtrahend).

        Returns:
            FExpression representing ``self - other``.

        Example:
            >>> F("revenue") - F("cost")
            >>> F("stock") - 1
        """
        return self._make_expression(operator.sub, other)

    def __mul__(self, other: Any) -> FExpression:
        """Create multiplication expression: ``self * other``.

        Args:
            other: Right-hand operand (multiplier).

        Returns:
            FExpression representing ``self * other``.

        Example:
            >>> F("price") * F("quantity")
            >>> F("amount") * Value(Decimal("0.09"))  # tax calculation
        """
        return self._make_expression(operator.mul, other)

    def __truediv__(self, other: Any) -> FExpression:
        """Create true division expression: ``self / other``.

        Args:
            other: Right-hand operand (divisor).

        Returns:
            FExpression representing ``self / other``.

        Example:
            >>> F("total") / F("count")          # average
            >>> F("profit") / F("revenue") * 100  # profit margin %

        Note:
            Database behavior for division by zero varies by backend:

            - **PostgreSQL**: Raises ``division_by_zero`` error.
            - **SQLite**: Returns ``NULL``.
            - **MySQL**: Returns ``NULL``.

            Consider using ``Case/When`` guards for safe division.
        """
        return self._make_expression(operator.truediv, other)

    def __mod__(self, other: Any) -> FExpression:
        """Create modulo expression: ``self % other``.

        Args:
            other: Right-hand operand (divisor for remainder).

        Returns:
            FExpression representing ``self % other``.

        Example:
            >>> F("row_number") % 2  # odd/even check
            >>> F("seconds") % 60    # remaining seconds
        """
        return self._make_expression(operator.mod, other)

    # ── Binary Arithmetic (Reflected) ───────────────────────────────

    def __radd__(self, other: Any) -> FExpression:
        """Create reflected addition: ``other + self``.

        Called when the left operand's ``__add__`` returns ``NotImplemented``.
        Since addition is commutative, the result is mathematically equivalent
        to ``self + other``, but operand order is preserved for SQL clarity.

        Args:
            other: Left-hand operand (typically a Python scalar).

        Returns:
            FExpression representing ``other + self``.

        Example:
            >>> 100 + F("bonus")  # int.__add__(F) → NotImplemented → F.__radd__(100)
        """
        return self._make_expression(operator.add, other, reverse=True)

    def __rsub__(self, other: Any) -> FExpression:
        """Create reflected subtraction: ``other - self``.

        Called when the left operand's ``__sub__`` returns ``NotImplemented``.
        Operand order matters here since subtraction is **not** commutative.

        Args:
            other: Left-hand operand (minuend).

        Returns:
            FExpression representing ``other - self``.

        Example:
            >>> 1000 - F("spent")  # remaining budget
            >>> # Generates: 1000 - "spent" column (NOT "spent" - 1000)
        """
        return self._make_expression(operator.sub, other, reverse=True)

    def __rmul__(self, other: Any) -> FExpression:
        """Create reflected multiplication: ``other * self``.

        Called when the left operand's ``__mul__`` returns ``NotImplemented``.
        Since multiplication is commutative, result is mathematically equivalent
        to ``self * other``.

        Args:
            other: Left-hand operand (typically a Python scalar).

        Returns:
            FExpression representing ``other * self``.

        Example:
            >>> 2 * F("quantity")  # double the quantity
        """
        return self._make_expression(operator.mul, other, reverse=True)

    def __rtruediv__(self, other: Any) -> FExpression:
        """Create reflected division: ``other / self``.

        Called when the left operand's ``__truediv__`` returns ``NotImplemented``.
        Operand order matters since division is **not** commutative.

        Args:
            other: Left-hand operand (dividend).

        Returns:
            FExpression representing ``other / self``.

        Example:
            >>> 1 / F("rate")  # inverse rate
            >>> # Generates: 1 / "rate" column (NOT "rate" / 1)
        """
        return self._make_expression(operator.truediv, other, reverse=True)

    def __rmod__(self, other: Any) -> FExpression:
        """Create reflected modulo: ``other % self``.

        Called when the left operand's ``__mod__`` returns ``NotImplemented``.
        Operand order matters since modulo is **not** commutative.

        Args:
            other: Left-hand operand (dividend for remainder).

        Returns:
            FExpression representing ``other % self``.

        Example:
            >>> 100 % F("divisor")
        """
        return self._make_expression(operator.mod, other, reverse=True)

    # ── Unary Operators ─────────────────────────────────────────────

    def __neg__(self) -> FExpression:
        """Create negation expression: ``-self``.

        Produces a unary :class:`FExpression` with ``None`` as the left operand
        and ``operator.neg`` as the operation. During resolution, FExpression
        recognizes the ``None`` left operand and applies unary negation.

        Returns:
            FExpression representing ``-self``.

        Example:
            >>> -F("balance")           # negate balance
            >>> -Sum("adjustments")     # negate aggregate result
            >>>
            >>> # Equivalent SQL: -"balance", -SUM("adjustments")
        """
        from async_orm_kit.query.expressions.f import FExpression

        return FExpression(None, operator.neg, self)

    def __abs__(self) -> FExpression:
        """Create absolute value expression: ``abs(self)``.

        Produces a unary :class:`FExpression` with ``None`` as the left operand
        and the string ``"abs"`` as the operation identifier. During resolution,
        FExpression maps ``"abs"`` to the ``func.abs()`` SQL function.

        Returns:
            FExpression representing ``ABS(self)``.

        Example:
            >>> abs(F("profit_loss"))       # absolute value of column
            >>> abs(F("actual") - F("expected"))  # absolute deviation
            >>>
            >>> # Equivalent SQL: ABS("profit_loss"), ABS("actual" - "expected")

        Note:
            Uses the string ``"abs"`` instead of ``operator.abs`` because
            Python's :mod:`operator` module does not provide an ``abs``
            function. The :class:`FExpression` resolver handles this string
            specially by delegating to ``sqlalchemy.func.abs()``.
        """
        from async_orm_kit.query.expressions.f import FExpression

        return FExpression(None, "abs", self)

    def __floordiv__(self, other):
        """Portable floor division using SQL FLOOR(lhs / rhs)."""
        return self._make_expression("floordiv", other)

    def __rfloordiv__(self, other):
        return self._make_expression("floordiv", other, reverse=True)

    def __and__(self, other):
        """Bitwise AND (SQLAlchemy's dialect-aware operator)."""
        return self._make_expression("bit_and", other)

    def __rand__(self, other):
        return self._make_expression("bit_and", other, reverse=True)

    def __or__(self, other):
        """Bitwise OR; Q expressions use their own logical OR."""
        return self._make_expression("bit_or", other)

    def __ror__(self, other):
        return self._make_expression("bit_or", other, reverse=True)

    def __invert__(self):
        """Bitwise NOT; Q expressions use their own logical negation."""
        from async_orm_kit.query.expressions.f import FExpression

        return FExpression(None, "bit_not", self)

    def eq(self, other):
        """Explicit SQL equality, even for Value objects with structural __eq__."""
        return self._make_expression(operator.eq, other)

    def ne(self, other):
        """Explicit SQL inequality, independent of Python object equality."""
        return self._make_expression(operator.ne, other)
