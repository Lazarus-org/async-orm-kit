"""Typed, parameterized SQL function expressions with a portable core API.

User-defined raw function names are intentionally not accepted: each wrapper
chooses a reviewed SQLAlchemy function/operator and resolves operands through
the same field resolver as ``F``, ``Case`` and aggregate expressions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import cast, extract, func
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy.types import TypeEngine

from async_orm_kit.query.lookups.resolver import ResolverContext
from async_orm_kit.query.mixins.combinable import CombinableMixin
from async_orm_kit.query.mixins.orderable import OrderableMixin


def _operand(value: Any, field_resolver: Any, context: ResolverContext):
    """Resolve ORM operands, preserving SQLAlchemy SQL and bound literals."""
    from sqlalchemy import literal

    from async_orm_kit.types import SAFE_SCALAR_TYPES

    if isinstance(value, CombinableMixin) and hasattr(value, "resolve"):
        return value.resolve(field_resolver=field_resolver, context=context)
    if isinstance(value, ClauseElement):
        return value
    if isinstance(value, SAFE_SCALAR_TYPES):
        return literal(value)
    raise TypeError(f"Unsupported SQL function operand: {type(value).__name__}")


class SQLFunction(CombinableMixin, OrderableMixin):
    """Base class for a fixed, reviewed SQL function with immutable operands."""

    _name: str
    _arguments: tuple[Any, ...]
    __slots__ = ("_name", "_arguments")
    _allowed = frozenset(
        {"coalesce", "nullif", "lower", "upper", "length", "trim", "round", "abs"}
    )

    def __init__(self, name: str, *arguments: Any) -> None:
        if name not in self._allowed:
            raise ValueError(f"Unsupported SQL function: {name!r}")
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_arguments", tuple(arguments))

    def __setattr__(self, key, value):
        raise AttributeError("SQL function expressions are immutable")

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        operands = [_operand(arg, field_resolver, context) for arg in self._arguments]
        return getattr(func, self._name)(*operands)


class Function(CombinableMixin, OrderableMixin):
    """Extensible SQL function with a trusted SQLAlchemy expression factory.

    A factory receives resolved, parameterized SQLAlchemy operands and must
    return a SQLAlchemy ColumnElement. Calling application-owned functions is
    intentional; the package never interpolates untrusted function names or SQL.

    Example::
        Function(lambda col: func.reverse(col), F("name"))
    """

    __slots__ = ("factory", "arguments")

    def __init__(self, factory: Any, *arguments: Any):
        if not callable(factory):
            raise TypeError("Function factory must be callable")
        self.factory = factory
        self.arguments = tuple(arguments)

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        from sqlalchemy.sql.elements import ColumnElement

        values = [_operand(arg, field_resolver, context) for arg in self.arguments]
        result = self.factory(*values)
        if not isinstance(result, ColumnElement):
            raise TypeError("Function factory must return a SQLAlchemy ColumnElement")
        return result


class Coalesce(SQLFunction):
    """Return first non-NULL expression."""

    def __init__(self, *expressions: Any):
        if len(expressions) < 2:
            raise ValueError("Coalesce requires at least two expressions")
        super().__init__("coalesce", *expressions)


class NullIf(SQLFunction):
    """Return NULL when expressions compare equal."""

    def __init__(self, first: Any, second: Any):
        super().__init__("nullif", first, second)


class Lower(SQLFunction):
    def __init__(self, expression: Any):
        super().__init__("lower", expression)


class Upper(SQLFunction):
    def __init__(self, expression: Any):
        super().__init__("upper", expression)


class Length(SQLFunction):
    def __init__(self, expression: Any):
        super().__init__("length", expression)


class Trim(SQLFunction):
    def __init__(self, expression: Any):
        super().__init__("trim", expression)


class Round(SQLFunction):
    def __init__(self, expression: Any, precision: Any = 0):
        super().__init__("round", expression, precision)


class Abs(SQLFunction):
    def __init__(self, expression: Any):
        super().__init__("abs", expression)


class Concat(CombinableMixin, OrderableMixin):
    """Concatenate strings using the dialect's string concatenation operator."""

    __slots__ = ("arguments",)

    def __init__(self, *expressions: Any):
        if len(expressions) < 2:
            raise ValueError("Concat requires at least two expressions")
        self.arguments = tuple(expressions)

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        values = [_operand(arg, field_resolver, context) for arg in self.arguments]
        out = values[0]
        for value in values[1:]:
            out = out.concat(value)
        return out


class Cast(CombinableMixin, OrderableMixin):
    """Cast an expression to a validated SQLAlchemy SQL type."""

    __slots__ = ("expression", "type_")

    def __init__(self, expression: Any, type_: TypeEngine | type[TypeEngine]):
        if isinstance(type_, type) and issubclass(type_, TypeEngine):
            type_ = type_()
        if not isinstance(type_, TypeEngine):
            raise TypeError("Cast type_ must be a SQLAlchemy TypeEngine")
        self.expression = expression
        self.type_ = type_

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        return cast(_operand(self.expression, field_resolver, context), self.type_)


class Extract(CombinableMixin, OrderableMixin):
    """Extract a supported date/time part with SQLAlchemy dialect compilation."""

    __slots__ = ("part", "expression")
    _parts = frozenset(
        {"year", "month", "day", "hour", "minute", "second", "dow", "week", "quarter"}
    )

    def __init__(self, part: str, expression: Any):
        if part not in self._parts:
            raise ValueError(f"Unsupported Extract part: {part!r}")
        self.part = part
        self.expression = expression

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        return extract(self.part, _operand(self.expression, field_resolver, context))


__all__ = [
    "Abs",
    "Cast",
    "Coalesce",
    "Concat",
    "Extract",
    "Length",
    "Lower",
    "Function",
    "NullIf",
    "Round",
    "SQLFunction",
    "Trim",
    "Upper",
]
