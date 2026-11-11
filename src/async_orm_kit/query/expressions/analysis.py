"""SQL expression analysis independent of query-side mutation.

Inspect *resolved* SQLAlchemy expressions rather than guessing from a wrapper's
outer type. In particular, ``Sum(...) + 1`` and CASE nested sums are aggregates,
whereas ``SUM(...) OVER (...)`` is a window expression, not a GROUP BY aggregate.
"""

from __future__ import annotations

from sqlalchemy.sql.elements import ClauseElement, Over
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.selectable import Exists as SAExists, ScalarSelect, SelectBase

_AGGREGATES = frozenset(
    {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "array_agg",
        "string_agg",
        "bool_and",
        "bool_or",
        "every",
        "json_agg",
        "jsonb_agg",
    }
)


def contains_aggregate(expression: ClauseElement) -> bool:
    """Return whether SQL contains a non-window aggregate call."""

    def visit(node: ClauseElement) -> bool:
        if isinstance(node, (Over, ScalarSelect, SAExists, SelectBase)):
            # A nested SELECT owns its own grouping/window context. Walking into
            # it would erroneously mark the containing queryset as aggregate.
            return False
        if getattr(node, "_annotations", {}).get("async_orm_kit_aggregate") is True:
            return True
        if isinstance(node, FunctionElement) and str(node.name).lower() in _AGGREGATES:
            return True
        return any(visit(child) for child in node.get_children())

    return visit(expression)


def contains_window(expression: ClauseElement) -> bool:
    """Determine whether expression includes SQL OVER window clauses."""
    if isinstance(expression, Over):
        return True
    if isinstance(expression, (ScalarSelect, SAExists, SelectBase)):
        # Window functions inside a subquery are legal in the outer WHERE.
        return False
    return any(contains_window(child) for child in expression.get_children())
