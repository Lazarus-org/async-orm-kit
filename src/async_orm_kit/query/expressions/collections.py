"""Typed JSON and PostgreSQL ARRAY expressions.

These expressions resolve mapped columns through the normal field resolver; JSON
path selectors use SQLAlchemy's dialect-aware JSON operators (not string-built
SQL). PostgreSQL-only operators require PostgreSQL column types and compile with
bound parameters. Unsupported combinations raise instead of falling back to
LIKE comparisons or incorrect indexes.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import Integer, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.sqltypes import JSON
from sqlalchemy.sql.visitors import InternalTraversal

from async_orm_kit.query.expressions.functions import _operand
from async_orm_kit.query.lookups.resolver import ResolverContext
from async_orm_kit.query.mixins.combinable import CombinableMixin
from async_orm_kit.query.mixins.orderable import OrderableMixin


class _PostgreSQLOnly(ColumnElement):
    """Prevent PostgreSQL-only predicates from silently compiling elsewhere."""

    inherit_cache = True
    _traverse_internals = [("_wrapped", InternalTraversal.dp_clauseelement)]

    def __init__(self, expression):
        self._wrapped = expression
        self.type = expression.type


@compiles(_PostgreSQLOnly, "postgresql")
def _compile_postgresql(element, compiler, **kwargs):
    return compiler.process(element._wrapped, **kwargs)


@compiles(_PostgreSQLOnly)
def _reject_other_dialects(element, compiler, **kwargs):
    raise CompileError("This JSONB/ARRAY operation is supported only on PostgreSQL")


class _TypedExpression(CombinableMixin, OrderableMixin):
    __slots__ = ()


def _json_source(expression: Any, resolver: Any, context: ResolverContext):
    source = _operand(expression, resolver, context)
    if not isinstance(source.type, JSON):
        raise TypeError("JSON expressions require a SQLAlchemy JSON/JSONB column")
    return source


def _postgres_array(expression: Any, resolver: Any, context: ResolverContext):
    source = _operand(expression, resolver, context)
    if not isinstance(source.type, ARRAY):
        raise TypeError("ARRAY expressions require a PostgreSQL ARRAY column")
    return source


class JSONPath(_TypedExpression):
    """Extract a JSON path, retaining an explicit result type.

    ``JSONPath(F('payload'), 'customer', 'id', as_type='integer')`` compiles
    with the JSON operators of the active SQLAlchemy dialect. The path is a
    SQLAlchemy JSON index parameter, not interpolated SQL text.

    ``as_type`` can be ``json``, ``string``, ``integer``, ``float`` or
    ``boolean``. JSON null and SQL NULL have distinct semantics and should be
    tested for each intended dialect.
    """

    __slots__ = ("expression", "path", "as_type")
    _types = frozenset({"json", "string", "integer", "float", "boolean"})

    def __init__(
        self,
        expression: Any,
        *path: str | int,
        as_type: Literal["json", "string", "integer", "float", "boolean"] = "json",
    ):
        if not path:
            raise ValueError("JSONPath requires at least one path segment")
        if any(
            isinstance(part, bool)
            or not isinstance(part, (str, int))
            or (isinstance(part, str) and not part)
            or (isinstance(part, int) and part < 0)
            for part in path
        ):
            raise ValueError(
                "JSONPath segments must be non-empty keys or nonnegative indexes"
            )
        if as_type not in self._types:
            raise ValueError(f"Unsupported JSON result type: {as_type!r}")
        self.expression = expression
        self.path = tuple(path)
        self.as_type = as_type

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        source = _json_source(self.expression, field_resolver, context)
        # Multi-segment JSON paths compile to #> for PostgreSQL and
        # json_extract/JSON_EXTRACT for SQLite/MySQL.
        selected = source[self.path[0] if len(self.path) == 1 else self.path]
        if self.as_type == "json":
            return selected
        return getattr(selected, f"as_{self.as_type}")()


class JSONHasKey(_TypedExpression):
    """PostgreSQL JSONB top-level key-existence operator ``?``.

    The JSONB comparator is required to prevent SQLAlchemy's generic string
    containment behavior from being used on SQLite or MySQL JSON values.
    """

    __slots__ = ("expression", "key")

    def __init__(self, expression: Any, key: str):
        if not isinstance(key, str) or not key:
            raise ValueError("JSONHasKey requires a non-empty key")
        self.expression = expression
        self.key = key

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        source = _json_source(self.expression, field_resolver, context)
        if not isinstance(source.type, JSONB):
            raise TypeError("JSONHasKey requires a PostgreSQL JSONB column")
        return _PostgreSQLOnly(source.has_key(self.key))  # noqa: W601


class JSONContains(_TypedExpression):
    """PostgreSQL JSONB containment ``@>`` with a bound JSON value."""

    __slots__ = ("expression", "value")

    def __init__(self, expression: Any, value: dict | list):
        if not isinstance(value, (dict, list)):
            raise TypeError("JSONContains expects a JSON object or array")
        self.expression = expression
        self.value = value

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        source = _json_source(self.expression, field_resolver, context)
        if not isinstance(source.type, JSONB):
            raise TypeError("JSONContains requires a PostgreSQL JSONB column")
        return _PostgreSQLOnly(source.contains(self.value))


class ArrayIndex(_TypedExpression):
    """Index a PostgreSQL ARRAY using the SQLAlchemy column's index convention.

    Normally PostgreSQL arrays are 1-indexed. If the mapped SQLAlchemy ARRAY
    has ``zero_indexes=True``, SQLAlchemy adjusts indexes automatically.
    """

    __slots__ = ("expression", "index")

    def __init__(self, expression: Any, index: int):
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("ArrayIndex requires a nonnegative integer")
        self.expression = expression
        self.index = index

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        source = _postgres_array(self.expression, field_resolver, context)
        if self.index == 0 and not source.type.zero_indexes:
            raise ValueError(
                "PostgreSQL ARRAY indexes start at 1 unless zero_indexes=True"
            )
        return _PostgreSQLOnly(source[self.index])


class ArrayContains(_TypedExpression):
    """PostgreSQL ARRAY containment ``@>``."""

    __slots__ = ("expression", "values")

    def __init__(self, expression: Any, values: list | tuple):
        if not isinstance(values, (list, tuple)):
            raise TypeError("ArrayContains expects a list or tuple")
        self.expression = expression
        self.values = list(values)

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        return _PostgreSQLOnly(
            _postgres_array(self.expression, field_resolver, context).contains(
                self.values
            )
        )


class ArrayOverlap(_TypedExpression):
    """PostgreSQL ARRAY overlap ``&&``."""

    __slots__ = ("expression", "values")

    def __init__(self, expression: Any, values: list | tuple):
        if not isinstance(values, (list, tuple)):
            raise TypeError("ArrayOverlap expects a list or tuple")
        self.expression = expression
        self.values = list(values)

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        return _PostgreSQLOnly(
            _postgres_array(self.expression, field_resolver, context).overlap(
                self.values
            )
        )


class ArrayLength(_TypedExpression):
    """PostgreSQL ``array_length(array, dimension)``."""

    __slots__ = ("expression", "dimension")

    def __init__(self, expression: Any, dimension: int = 1):
        if (
            isinstance(dimension, bool)
            or not isinstance(dimension, int)
            or dimension < 1
        ):
            raise ValueError("ArrayLength dimension must be a positive integer")
        self.expression = expression
        self.dimension = dimension

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        return _PostgreSQLOnly(
            func.array_length(
                _postgres_array(self.expression, field_resolver, context),
                self.dimension,
                type_=Integer(),
            )
        )


__all__ = [
    "ArrayContains",
    "ArrayIndex",
    "ArrayLength",
    "ArrayOverlap",
    "JSONContains",
    "JSONHasKey",
    "JSONPath",
]
