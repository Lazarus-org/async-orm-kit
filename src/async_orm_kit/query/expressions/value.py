from __future__ import annotations

from typing import Any

from sqlalchemy import literal

from async_orm_kit.query.lookups.resolver import FieldResolver, ResolverContext
from async_orm_kit.query.mixins.combinable import CombinableMixin
from async_orm_kit.query.mixins.orderable import OrderableMixin
from async_orm_kit.types import SAFE_SCALAR_TYPES


class Value(CombinableMixin, OrderableMixin):
    """Wraps a literal scalar value for use in database expressions.

    Value objects convert Python scalars (str, int, float, bool, None, datetime, etc.)
    to SQLAlchemy literal() expressions, enabling their use in annotations, Case/When
    expressions, and F expressions. They ensure type-safe SQL generation by wrapping
    literals that would otherwise be ambiguous or require special handling.

    Common use cases include providing default values in Case expressions, creating
    constant annotations, and mixing literals with F objects in arithmetic operations.
    Value objects participate in the same expression system as F objects, supporting
    arithmetic operations and comparisons through CombinableMixin.

    Args:
        value: Python scalar value to wrap. Supports str, int, float, bool, None,
               datetime, date, time, Decimal, and other SQLAlchemy-compatible types.

    Note:
        Value objects are immutable and hashable, making them safe to reuse across
        multiple queries. They resolve to SQLAlchemy literal() expressions at query
        compilation time, ensuring proper SQL escaping and type handling.

        Unlike F objects which reference database columns, Value objects represent
        constant values that are embedded directly in the SQL query. This distinction
        is important for understanding query behavior and optimization.
    """

    _value: Any
    __slots__ = ("_value",)

    @property
    def value(self) -> Any:
        return self._value

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Value expressions are immutable.")

    def __init__(self, value: Any):
        """Initialize Value with a Python scalar.

        Args:
            value: Scalar value to wrap. Common types: str, int, float, bool, None,
                   datetime, date, Decimal.
        Example:
            >>> Value("Active")
            >>> Value(42)
            >>> Value(3.14)
            >>> Value(None)
        """

        if not isinstance(value, SAFE_SCALAR_TYPES):
            raise TypeError(
                f"Value() only accepts supported scalar values, got {type(value).__name__}."
            )
        object.__setattr__(self, "_value", value)

    def resolve(self, field_resolver: FieldResolver, context: ResolverContext):
        """Resolve to SQLAlchemy literal expression.

        Converts the wrapped Python value to a SQLAlchemy literal() expression,
        which handles proper SQL escaping and type conversion based on the database
        dialect. The literal is embedded directly in the generated SQL.

        Args:
            field_resolver: FieldResolver instance (unused, for interface consistency).
            context: ResolverContext (unused, for interface consistency).

        Returns:
            SQLAlchemy literal expression representing the wrapped value.

        Example:
            >>> # Internal usage during query compilation
            >>> v = Value("Active")
            >>> literal_expr = v.resolve(field_resolver, context)
            >>> # Returns: literal("Active") -> SQL: 'Active'
        """
        return literal(self.value)

    def __repr__(self):
        """Return string representation showing wrapped value."""
        return f"Value({self.value!r})"

    def __eq__(self, other):
        """Equality based on wrapped value for comparison and deduplication."""
        return isinstance(other, Value) and self.value == other.value

    def __hash__(self):
        """Return hash based on wrapped value for use in sets and dicts."""
        return hash(("Value", self.value))
