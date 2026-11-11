"""Lazy, immutable SQL set operations over compatible QuerySets.

UNION, UNION ALL, INTERSECT and EXCEPT are SQL set operations: unlike joining
related objects, they combine equal-shaped rows. The wrapper deliberately
limits operations to compatible QuerySets bound to the SAME session and leaves
SQLAlchemy in charge of SQL rendering. SQL query values remain bound parameters.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import (
    column,
    except_,
    func,
    inspect as sa_inspect,
    intersect,
    select,
    union,
    union_all,
)
from sqlalchemy.sql.elements import ColumnElement

from async_orm_kit.exceptions import DatabaseError, QueryStateError

_OPERATIONS = {
    "union": union,
    "union_all": union_all,
    "intersect": intersect,
    "except": except_,
}


def _python_result_type(column) -> type | None:
    """Best-effort SQLAlchemy type check, without evaluating bound SQL values.

    Dialect-specific/custom types may not expose ``python_type``. In that case
    the database remains the source of truth for compound-column coercion.
    """
    try:
        return column.type.python_type
    except (AttributeError, NotImplementedError, TypeError):
        return None


def _query_shape(queryset):
    state = queryset._state
    if state.for_update is not None:
        raise QueryStateError("Set operations do not support row locking")
    if state.select_related or state.prefetch_related:
        raise QueryStateError("Set operations do not support eager-loading directives")
    if state.order_by or state.limit_val is not None or state.offset_val is not None:
        raise QueryStateError(
            "Set-operation branches cannot have ORDER BY, LIMIT or OFFSET; "
            "apply them to the combined result instead"
        )
    if state.distinct_on:
        raise QueryStateError("Set operations do not support DISTINCT ON branches")

    if state.values_select is not None:
        keys = tuple(name for name, _ in state.values_select)
        return "values", keys + tuple(
            key for key in state.annotations if key not in keys
        )
    if state.values_list_select is not None:
        keys = tuple(name for name, _ in state.values_list_select)
        return ("flat" if state.values_list_flat else "tuples"), (
            keys + tuple(key for key in state.annotations if key not in keys)
        )
    if state.annotations or state.only_fields is not None:
        raise QueryStateError(
            "Model set operations do not support annotations or deferred fields; "
            "use matching values() or values_list() projections"
        )
    mapper = sa_inspect(queryset.model)
    mapper = mapper.mapper if mapper.is_aliased_class else mapper
    return "model", tuple(attr.key for attr in mapper.column_attrs)


class CompoundQuerySet:
    """Result of combining compatible QuerySets with SQL set operators.

    The result is awaitable and supports ``.order_by()``, ``.limit()``,
    ``.offset()``, ``.count()``, ``.exists()``, ``.sql()`` and streaming.
    ``filter()`` and ``exclude()`` apply to the compound result after the set
    operation, before ordering and pagination. Only projected names are valid;
    this does not implicitly join related tables.
    """

    __slots__ = (
        "model",
        "session",
        "_mode",
        "_fields",
        "_statement",
        "_cache",
    )

    def __init__(self, querysets, operation: str):
        from async_orm_kit.queryset.queryset import QuerySet

        if operation not in _OPERATIONS:
            raise ValueError(f"Unsupported set operation: {operation}")
        if len(querysets) < 2 or not all(isinstance(qs, QuerySet) for qs in querysets):
            raise TypeError("Set operations require at least two QuerySets")
        first = querysets[0]
        shape = _query_shape(first)
        for other in querysets[1:]:
            if other.session is not first.session:
                raise ValueError(
                    "Set-operation branches must use the same AsyncSession"
                )
            if other.model is not first.model:
                raise ValueError(
                    "Set-operation branches must query the same mapped model"
                )
            if _query_shape(other) != shape:
                raise QueryStateError(
                    "Set-operation branches must have matching projections"
                )

        self.model = first.model
        self.session = first.session
        self._mode, self._fields = shape
        statements = [qs._compiler.compile(qs.state) for qs in querysets]
        widths = [len(stmt.selected_columns) for stmt in statements]
        if len(set(widths)) != 1:
            raise QueryStateError(
                "Set-operation branches must return an identical number of SQL columns"
            )
        # Equal labels and column counts are insufficient for compound SELECTs.
        # For example, UNION of an integer-valued annotation with a text-valued
        # annotation can return rows with inconsistent Python value types (or
        # produce a dialect-specific coercion error). Reject known incompatible
        # Python result types before execution; leave custom/unknown SQLAlchemy
        # types to the database rather than guessing about their compatibility.
        expected_types = tuple(
            _python_result_type(column) for column in statements[0].selected_columns
        )
        for statement in statements[1:]:
            for index, (expected, selected_column) in enumerate(
                zip(expected_types, statement.selected_columns, strict=False), start=1
            ):
                actual = _python_result_type(selected_column)
                if (
                    expected is not None
                    and actual is not None
                    and expected is not actual
                ):
                    raise QueryStateError(
                        f"Set-operation column {index} has incompatible result "
                        f"types: {expected.__name__} versus {actual.__name__}; "
                        "cast projections explicitly to a common type"
                    )
        self._statement = _OPERATIONS[operation](*statements)
        self._cache = None

    def _clone(self):
        clone = object.__new__(type(self))
        clone.model = self.model
        clone.session = self.session
        clone._mode = self._mode
        clone._fields = self._fields
        clone._statement = self._statement
        clone._cache = None
        return clone

    def _filter_result(self, predicate, *, negate: bool):
        from sqlalchemy import and_, or_

        from async_orm_kit.query.lookups import LOOKUP_OPERATORS, STANDARD_LOOKUPS
        from async_orm_kit.query.lookups.conditions import ConditionBuilder
        from async_orm_kit.query.lookups.json_paths import json_lookup
        from async_orm_kit.query.lookups.resolver import ResolverContext
        from async_orm_kit.query.q import Q

        statement = self._statement
        if (
            statement._limit_clause is not None
            or statement._offset_clause is not None
            or tuple(statement._order_by_clauses)
        ):
            raise QueryStateError(
                "Apply filter()/exclude() before order_by(), limit() and offset() "
                "on a set-operation result"
            )
        source = statement.subquery()

        class _Resolver:
            query_state = type("_State", (), {"custom_lookups": {}})()

        builder = ConditionBuilder(_Resolver())

        def leaf(key, value):
            if not isinstance(key, str) or not key:
                raise TypeError("Compound filters require nonempty field lookup names")
            name, _, lookup = key.partition("__")
            if name not in self._fields or name not in source.c:
                raise QueryStateError(
                    f"Field {name!r} is not projected by the set result"
                )
            field = source.c[name]
            lookup = lookup or "exact"
            if getattr(value, "resolve", None) is not None:
                raise QueryStateError("Compound filters require literal lookup values")
            resolved = json_lookup(field, lookup, value)
            if isinstance(resolved, ColumnElement):
                return resolved
            if resolved is not None:
                field, lookup = resolved
            if lookup not in LOOKUP_OPERATORS and lookup not in STANDARD_LOOKUPS:
                raise QueryStateError(f"Unsupported compound result lookup: {lookup!r}")
            return builder.build(field, lookup, value, context=ResolverContext())

        def resolve(q):
            clauses = []
            for child in q.children:
                if isinstance(child, Q):
                    clauses.append(resolve(child))
                else:
                    _, key, value = child
                    clauses.append(leaf(key, value))
            if not clauses:
                return None
            result = or_(*clauses) if q.connector == Q.OR else and_(*clauses)
            return result.is_not(True) if q.negated else result

        expression = resolve(predicate)
        if expression is None:
            return self._clone()
        if negate:
            expression = expression.is_not(True)
        clone = self._clone()
        clone._statement = select(*(source.c[key] for key in self._fields)).where(
            expression
        )
        return clone

    def filter(self, *conditions, **lookups):
        """Filter the combined result by projected fields before pagination."""
        from async_orm_kit.query.q import Q

        if any(not isinstance(arg, Q) for arg in conditions):
            raise TypeError("Compound filter() accepts only positional Q objects")
        q = Q(*conditions, **lookups)
        return self._filter_result(q, negate=False)

    def exclude(self, *conditions, **lookups):
        """Exclude matching combined rows, retaining nullable comparisons."""
        from async_orm_kit.query.q import Q

        if any(not isinstance(arg, Q) for arg in conditions):
            raise TypeError("Compound exclude() accepts only positional Q objects")
        q = Q(*conditions, **lookups)
        return self._filter_result(q, negate=True)

    def order_by(self, *fields: str):
        """Order by a projected column name; ``-field`` means descending."""
        clone = self._clone()
        clauses = []
        for field in fields:
            if not isinstance(field, str) or not field:
                raise TypeError(
                    "Set operation order_by fields must be non-empty strings"
                )
            key = field[1:] if field.startswith("-") else field
            if not key or key not in self._fields:
                raise ValueError(
                    f"Set operation cannot order by unknown column {field!r}"
                )
            expr: Any = column(key)
            clauses.append(expr.desc() if field.startswith("-") else expr.asc())
        clone._statement = clone._statement.order_by(None).order_by(*clauses)
        return clone

    def limit(self, value: int):
        if type(value) is not int or value < 0:
            raise ValueError("limit must be a nonnegative integer")
        clone = self._clone()
        clone._statement = clone._statement.limit(value)
        return clone

    def offset(self, value: int):
        if type(value) is not int or value < 0:
            raise ValueError("offset must be a nonnegative integer")
        clone = self._clone()
        clone._statement = clone._statement.offset(value)
        return clone

    def sql(self, dialect=None) -> str:
        """Compile parameterized SQL without interpolating user values."""
        if dialect is None:
            bind = getattr(self.session, "bind", None)
            if bind is not None:
                dialect = bind.dialect
        return str(self._statement.compile(dialect=dialect))

    def cte(self, name: str | None = None):
        """Expose the resulting set operation as a SQLAlchemy CTE."""
        return self._statement.cte(name=name)

    def subquery(self, name: str | None = None):
        """Expose the resulting set operation as a SQLAlchemy subquery."""
        return self._statement.subquery(name=name)

    def _execution_statement(self):
        if self._mode == "model":
            # A set operation selects mapped table columns, not ORM entities.
            # from_statement() hydrates them through SQLAlchemy's identity map.
            return select(self.model).from_statement(self._statement)
        return self._statement

    def _convert(self, row):
        if self._mode == "model":
            return row[0]
        if self._mode == "values":
            return dict(zip(self._fields, row, strict=False))
        if self._mode == "flat":
            return row[0]
        return tuple(row)

    async def _fetch(self):
        if self._cache is not None:
            return self._cache
        try:
            result = await self.session.execute(self._execution_statement())
            rows = result.unique().all() if self._mode == "model" else result.all()
            self._cache = [self._convert(row) for row in rows]
            return self._cache
        except Exception as exc:
            raise DatabaseError("Error executing SQL set operation", cause=exc) from exc

    def __await__(self):
        return self._fetch().__await__()

    async def first(self):
        rows = await self.limit(1)
        return rows[0] if rows else None

    async def count(self) -> int:
        """Count the rows returned by the set operation including its pagination."""
        stmt = select(func.count()).select_from(self._statement.subquery())
        try:
            return (await self.session.execute(stmt)).scalar_one()
        except Exception as exc:
            raise DatabaseError("Error counting SQL set operation", cause=exc) from exc

    async def exists(self) -> bool:
        stmt = select(self._statement.exists())
        try:
            return bool((await self.session.execute(stmt)).scalar_one())
        except Exception as exc:
            raise DatabaseError("Error checking SQL set operation", cause=exc) from exc

    async def stream(self) -> AsyncIterator[Any]:
        """Stream set-operation results and close the SQLAlchemy cursor."""
        try:
            result = await self.session.stream(self._execution_statement())
        except Exception as exc:
            raise DatabaseError(
                "Error opening SQL set-operation stream", cause=exc
            ) from exc

        try:
            iterator = result.unique() if self._mode == "model" else result
            async for row in iterator:
                yield self._convert(row)
        except Exception as exc:
            raise DatabaseError("Error streaming SQL set operation", cause=exc) from exc
        finally:
            await result.close()

    def __aiter__(self):
        return self.stream()


__all__ = ["CompoundQuerySet"]
