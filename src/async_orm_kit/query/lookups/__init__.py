"""Built-in, validated lookup operators for QuerySet filters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import extract, func, literal
from sqlalchemy.sql.selectable import SelectBase
from sqlalchemy.sql.sqltypes import Date, DateTime, Time


def _lookup_in(column: Any, value: Any):
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise TypeError("The 'in' lookup requires a non-string iterable or SELECT.")
    if not isinstance(value, SelectBase) and not isinstance(value, Iterable):
        raise TypeError("The 'in' lookup requires an iterable or SELECT.")
    return column.in_(value)


def _lookup_range(column: Any, value: Any):
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise TypeError("The 'range' lookup requires exactly two boundary values.")
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise TypeError("The 'range' lookup requires exactly two boundary values.")
    return column.between(value[0], value[1])


def _lookup_iexact(column: Any, value: Any):
    if not isinstance(value, str):
        raise TypeError("The 'iexact' lookup requires a string value.")
    return func.lower(column) == func.lower(literal(value))


def _lookup_isnull(column: Any, value: Any):
    if type(value) is not bool:
        raise TypeError("The 'isnull' lookup requires a bool value.")
    return column.is_(None) if value else column.is_not(None)


def _lookup_date_part(column: Any, part: str, value: Any):
    """Compare a real date/time column part without dynamic SQL identifiers."""
    sql_type = getattr(column, "type", None)
    if sql_type is None and hasattr(column, "property"):
        columns = getattr(column.property, "columns", ())
        if columns:
            sql_type = columns[0].type
    if not isinstance(sql_type, (Date, DateTime, Time)):
        raise TypeError(f"The '{part}' lookup requires a date/time column")
    if type(value) is not int:
        raise TypeError(f"The '{part}' lookup requires an integer value")
    return extract(part, column) == value


LOOKUP_OPERATORS = {
    "exact": lambda column, value: column == value,
    "year": lambda column, value: _lookup_date_part(column, "year", value),
    "month": lambda column, value: _lookup_date_part(column, "month", value),
    "day": lambda column, value: _lookup_date_part(column, "day", value),
    "hour": lambda column, value: _lookup_date_part(column, "hour", value),
    "minute": lambda column, value: _lookup_date_part(column, "minute", value),
    "second": lambda column, value: _lookup_date_part(column, "second", value),
    "iexact": _lookup_iexact,
    "gt": lambda column, value: column > value,
    "gte": lambda column, value: column >= value,
    "lt": lambda column, value: column < value,
    "lte": lambda column, value: column <= value,
    "in": _lookup_in,
    "not_in": lambda column, value: ~_lookup_in(column, value),
    "not__in": lambda column, value: ~_lookup_in(column, value),
    "in_": _lookup_in,
    "range": _lookup_range,
    "between": _lookup_range,
    "isnull": _lookup_isnull,
}

# Only comparison/pattern operators are accepted as delegated SQLAlchemy methods.
# Methods such as asc(), desc(), op(), label(), and collate() are intentionally
# excluded because they do not produce boolean predicates.
STANDARD_LOOKUPS = frozenset(
    {
        "like",
        "ilike",
        "not_like",
        "not_ilike",
        "contains",
        "icontains",
        "startswith",
        "endswith",
        "istartswith",
        "iendswith",
        "match",
        "regexp_match",
        "is_",
        "is_not",
        "is_distinct_from",
        "isnot_distinct_from",
    }
)

FORBIDDEN_COLUMN_ATTRS = {
    "metadata",
    "table",
    "expression",
    "comparator",
}


__all__ = [
    "annotations",
    "Iterable",
    "Mapping",
    "Any",
    "extract",
    "func",
    "literal",
    "SelectBase",
    "Date",
    "DateTime",
    "Time",
]
