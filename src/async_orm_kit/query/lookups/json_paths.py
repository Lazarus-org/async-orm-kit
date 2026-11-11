"""Django-style JSON key/index traversal backed by dialect-aware SQLAlchemy JSON.

Paths are SQLAlchemy bound JSON indexes; user-supplied segments never become SQL
identifiers. This module deliberately distinguishes scalar lookup operators from
JSON object/array containment and does not offer a generic JSON ``LIKE`` fallback.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import sqltypes
from sqlalchemy.sql.elements import ClauseElement

from async_orm_kit.exceptions import InvalidLookupError
from async_orm_kit.query.expressions.collections import _PostgreSQLOnly
from async_orm_kit.query.lookups import LOOKUP_OPERATORS, STANDARD_LOOKUPS

_JSONB_LOOKUPS = frozenset(
    {"contains", "has_key", "has_keys", "has_any_keys", "contained_by"}
)


def json_lookup(
    column: Any, raw_lookup: str, value: Any, *, resolver=None, context=None
):
    """Return a (column, lookup) pair, or a complete JSONB SQL predicate.

    ``payload__user__age__gte=18`` resolves to integer JSON path extraction
    and a regular comparison; ``payload__tags__0='python'`` resolves to JSON
    array indexing. JSONB containment/key operators require PostgreSQL JSONB.
    """
    if not isinstance(getattr(column, "type", None), JSON):
        return None
    if raw_lookup == "exact":
        return None
    parts = raw_lookup.split("__")
    if any(not part for part in parts):
        raise InvalidLookupError(
            "JSON path segments cannot be empty", lookup=raw_lookup, field_name="<json>"
        )

    # Non-path JSONB operators must not be interpreted as string operations.
    operation = parts[-1]
    if operation in _JSONB_LOOKUPS and (
        operation != "contains" or isinstance(value, (dict, list))
    ):
        path_parts, operator = parts[:-1], operation
    elif operation in LOOKUP_OPERATORS or operation in STANDARD_LOOKUPS:
        path_parts, operator = parts[:-1], operation
    else:
        path_parts, operator = parts, "exact"

    path = tuple(int(part) if part.isdecimal() else part for part in path_parts)
    selected = column[path[0] if len(path) == 1 else path] if path else column

    if operator in _JSONB_LOOKUPS and (
        operator != "contains" or isinstance(value, (dict, list))
    ):
        if not isinstance(getattr(selected, "type", None), JSONB):
            raise InvalidLookupError(
                f"The JSON '{operator}' lookup requires a PostgreSQL JSONB column",
                lookup=operator,
                field_name="<json>",
            )
        if operator == "has_key" and (not isinstance(value, str) or not value):
            raise TypeError("JSONB has_key requires a nonempty string")
        if operator in {"has_keys", "has_any_keys"} and (
            not isinstance(value, (tuple, list))
            or not value
            or any(not isinstance(key, str) or not key for key in value)
        ):
            raise TypeError("JSONB key lookup requires a nonempty sequence of strings")
        method = {"has_keys": "has_all", "has_any_keys": "has_any"}.get(
            operator, operator
        )
        return _PostgreSQLOnly(getattr(selected, method)(value))

    if not path:
        # The root is a JSON document, not a string; reject accidental
        # substring matching which otherwise silently casts JSON to text.
        if operator in {
            "contains",
            "like",
            "ilike",
            "icontains",
            "startswith",
            "istartswith",
            "endswith",
            "iendswith",
            "match",
            "regexp_match",
        }:
            raise InvalidLookupError(
                "Use an explicit JSON path or JSONPath(..., as_type='string') for text lookups",
                lookup=operator,
                field_name="<json>",
            )
        return selected, operator

    # SQLAlchemy's JSON comparators preserve the native dialect-specific
    # extraction semantics. No SQL fragments are built from path elements.
    if operator == "isnull":
        return selected.as_string(), operator
    # Expressions such as F('score') are not Python int/str instances.
    # Infer the JSON scalar extractor from their resolved SQLAlchemy type,
    # otherwise SQLite JSON_QUOTE could compare text against a numeric field.
    sql_operand = None
    if callable(getattr(value, "resolve", None)) and resolver is not None:
        sql_operand = value.resolve(resolver, context)
    elif isinstance(value, ClauseElement):
        sql_operand = value
    if sql_operand is not None:
        operand_type = getattr(sql_operand, "type", None)
        if isinstance(operand_type, sqltypes.Boolean):
            return selected.as_boolean(), operator
        if isinstance(operand_type, sqltypes.Integer):
            return selected.as_integer(), operator
        if isinstance(operand_type, (sqltypes.Float, sqltypes.Numeric)):
            return selected.as_float(), operator
        if isinstance(operand_type, sqltypes.String):
            return selected.as_string(), operator
        raise InvalidLookupError(
            "JSON comparison expression needs an explicit typed JSONPath annotation",
            lookup=operator,
            field_name="<json>",
        )
    if operator in {"in", "not_in", "not__in", "in_", "range", "between"}:
        # Membership/range values determine the JSON extractor's scalar type,
        # never the Python collection wrapper itself.
        candidate = (
            next(iter(value), None) if isinstance(value, (list, tuple)) else None
        )
        if isinstance(candidate, bool):
            return selected.as_boolean(), operator
        if isinstance(candidate, int):
            return selected.as_integer(), operator
        if isinstance(candidate, float):
            return selected.as_float(), operator
        if isinstance(candidate, str):
            return selected.as_string(), operator
    if isinstance(value, bool):
        return selected.as_boolean(), operator
    if isinstance(value, int):
        return selected.as_integer(), operator
    if isinstance(value, float):
        return selected.as_float(), operator
    if isinstance(value, str) or operator in {
        "icontains",
        "contains",
        "startswith",
        "istartswith",
        "endswith",
        "iendswith",
        "like",
        "ilike",
        "iexact",
    }:
        return selected.as_string(), operator
    if value is None and operator == "exact":
        return selected.as_string(), operator
    return selected.as_json(), operator


__all__ = ["json_lookup"]
