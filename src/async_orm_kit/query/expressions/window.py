"""First-class SQL window expressions, without accidental GROUP BY injection."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func

from async_orm_kit.query.expressions.functions import _operand
from async_orm_kit.query.lookups.resolver import ResolverContext
from async_orm_kit.query.mixins.combinable import CombinableMixin
from async_orm_kit.query.mixins.orderable import OrderableMixin, OrderedExpression


class _WindowFunction(CombinableMixin, OrderableMixin):
    __slots__ = ("name", "arguments")
    _allowed = frozenset({"row_number", "rank", "dense_rank", "lag", "lead"})

    def __init__(self, name: str, *arguments: Any):
        if name not in self._allowed:
            raise ValueError(f"Unsupported window function: {name!r}")
        self.name = name
        self.arguments = tuple(arguments)

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        return getattr(func, self.name)(
            *[_operand(arg, field_resolver, context) for arg in self.arguments]
        )


class RowNumber(_WindowFunction):
    def __init__(self):
        super().__init__("row_number")


class Rank(_WindowFunction):
    def __init__(self):
        super().__init__("rank")


class DenseRank(_WindowFunction):
    def __init__(self):
        super().__init__("dense_rank")


class Lag(_WindowFunction):
    def __init__(self, expression: Any, offset: int = 1, default: Any = None):
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
            raise ValueError("Lag offset must be a positive integer")
        super().__init__("lag", expression, offset, default)


class Lead(_WindowFunction):
    def __init__(self, expression: Any, offset: int = 1, default: Any = None):
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
            raise ValueError("Lead offset must be a positive integer")
        super().__init__("lead", expression, offset, default)


class Window(CombinableMixin, OrderableMixin):
    """Apply OVER/PARTITION BY/ORDER BY to a SQL expression.

    ``order_by`` requires expressions with explicit ordering (`.asc()`/`.desc()`)
    or raw SQLAlchemy order expressions. A string field name can be specified
    using ``F('field').asc()`` rather than trusted raw SQL.
    """

    __slots__ = ("expression", "partition_by", "order_by")

    def __init__(self, expression: Any, *, partition_by=(), order_by=()):
        from sqlalchemy.sql.elements import ClauseElement

        if not (isinstance(expression, (CombinableMixin, ClauseElement))):
            raise TypeError("Window requires an ORM or SQLAlchemy SQL expression")
        self.expression = expression
        self.partition_by = tuple(partition_by)
        self.order_by = tuple(order_by)
        if isinstance(expression, (RowNumber, Rank, DenseRank)) and not self.order_by:
            raise ValueError("Ranking window expressions require order_by")

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        partition = [_operand(x, field_resolver, context) for x in self.partition_by]
        ordering = [
            x.resolve(field_resolver, context)
            if isinstance(x, OrderedExpression)
            else _operand(x, field_resolver, context)
            for x in self.order_by
        ]
        return _operand(self.expression, field_resolver, context).over(
            partition_by=partition or None,
            order_by=ordering or None,
        )


__all__ = ["DenseRank", "Lag", "Lead", "Rank", "RowNumber", "Window"]
