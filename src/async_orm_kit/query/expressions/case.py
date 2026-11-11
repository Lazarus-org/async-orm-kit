from __future__ import annotations

from typing import Any

from sqlalchemy import and_, case, literal

from async_orm_kit._logging import get_logger
from async_orm_kit.exceptions import FilterError
from async_orm_kit.query.lookups.resolver import FieldResolver, ResolverContext
from async_orm_kit.query.mixins.combinable import CombinableMixin
from async_orm_kit.query.mixins.orderable import OrderableMixin
from async_orm_kit.types import SAFE_SCALAR_TYPES

logger = get_logger(__name__)


class When:
    """
    Represents a single WHEN ... THEN ... branch in a CASE expression.

    Supports both kwargs-style conditions and Q objects:

        When(status="active", then=Value("Active"))
        When(Q(score__gte=90) | Q(is_vip=True), then=Value("Priority"))
        When(Q(price__gt=100), category="electronics", then=Value("Premium"))

    Args:
        *args:       Q() objects for complex boolean conditions.
        then:        The output value when the condition matches.
        **condition: Filter-style keyword arguments (same as .filter()).

    Raises:
        ValueError: If no conditions provided (neither Q objects nor kwargs).
        TypeError:  If positional args are not Q() instances.
    """

    __slots__ = ("q_objects", "condition", "then")

    def __init__(self, *args, then: Any, **condition):
        from async_orm_kit.query.q import Q

        # Validate positional args are Q objects
        invalid_args = [a for a in args if not isinstance(a, Q)]
        if invalid_args:
            raise TypeError(
                f"When() positional arguments must be Q() instances. "
                f"Got: {[type(a).__name__ for a in invalid_args]}"
            )

        if not args and not condition:
            raise ValueError(
                "When() requires at least one condition — "
                "either Q() objects or keyword arguments. "
                "Example: When(status='active', then=Value('Active'))"
            )
        if then is None:
            raise ValueError(
                "When() 'then' argument cannot be None. "
                "To represent a SQL NULL, use Value(None)."
            )

        from async_orm_kit.query.q import _snapshot_value

        self.q_objects = args
        self.condition = {
            key: _snapshot_value(value) for key, value in condition.items()
        }
        self.then = then

    def resolve(
        self,
        field_resolver: FieldResolver,
        context: ResolverContext = ResolverContext(),
    ):
        """
        Resolve condition(s) and then-clause against the shared FieldResolver.

        Combines Q objects and kwargs into a single AND condition,
        matching Django's behavior where::

            When(Q(...), Q(...), field=val, then=...)

        is equivalent to::

            When(Q(...) & Q(...) & Q(field=val), then=...)
        """
        from async_orm_kit.query.lookups.parser import FilterParser

        all_conditions = []

        # 1. Resolve Q() objects
        for q in self.q_objects:
            resolved = q.resolve(field_resolver=field_resolver, context=context)
            if resolved is not None:
                all_conditions.append(resolved)

        # 2. Resolve kwargs via FilterParser
        if self.condition:
            parser = FilterParser(field_resolver)
            parsed = parser.parse(self.condition, context=context)
            all_conditions.extend(parsed)

        if not all_conditions:
            raise ValueError("When() resolved to zero conditions")

        if len(all_conditions) == 1:
            resolved_condition = all_conditions[0]
        else:
            resolved_condition = and_(*all_conditions)

        # 3. Resolve the THEN clause
        resolved_then = _resolve_expression(self.then, field_resolver, context)

        return resolved_condition, resolved_then

    def __eq__(self, other):
        if not isinstance(other, When):
            return False
        return _when_key(self) == _when_key(other)

    def __hash__(self):
        return hash(("When", _when_key(self)))


def _when_key(branch):
    """Structural key without invoking SQL-expression overloaded equality.

    SQLAlchemy ClauseElements are deliberately identified by object identity;
    the ORM's own F/Value/FExpression nodes receive stable structural keys.
    """
    from async_orm_kit.query.expressions.f import F, FExpression
    from async_orm_kit.query.expressions.value import Value
    from async_orm_kit.query.q import _freeze_value

    def freeze(value):
        if isinstance(value, F):
            return ("F", value.name)
        if isinstance(value, FExpression):
            op = value.operator
            return (
                "FExpression",
                op if isinstance(op, str) else getattr(op, "__name__", str(op)),
                freeze(value.left),
                freeze(value.right),
            )
        if isinstance(value, Value):
            return ("Value", _freeze_value(value.value))
        if isinstance(value, (tuple, list)):
            return (type(value).__name__, tuple(freeze(v) for v in value))
        if isinstance(value, dict):
            return (
                "dict",
                tuple(
                    sorted(((freeze(k), freeze(v)) for k, v in value.items()), key=repr)
                ),
            )
        return _freeze_value(value)

    return freeze((branch.q_objects, branch.condition, branch.then))


class Case(CombinableMixin, OrderableMixin):
    """
    Represents a SQL CASE WHEN ... THEN ... [ELSE ...] END expression.

    Can be used anywhere an expression is accepted:
    - annotate()                    -> adds a computed column
    - aggregate functions (Sum, Count, etc.)  -> conditional aggregation
    - order_by()                    -> conditional ordering
    - update()                      -> conditional bulk update

    Args:
        *whens:  One or more When() instances defining the branches.
                 Evaluated left to right; first matching branch wins.
        default: The ELSE value/expression. Defaults to SQL NULL if omitted.
                 Can be Value(), F(), or another Case().

    Raises:
        ValueError: If no When() branches are provided.
        TypeError:  If positional args contain non-When() instances.

    .. code-block:: python

        # Basic label mapping
        Case(
            When(status="active",  then=Value("Active")),
            When(status="banned",  then=Value("Banned")),
            default=Value("Unknown"),
        )

        # Conditional aggregation
        Sum(Case(
            When(amount__gte=1000, then=Value(1)),
            default=Value(0),
        ))

        # Nested Case
        Case(
            When(score__gte=90, then=Value("A")),
            When(score__gte=70, then=Case(
                When(attendance__gte=80, then=Value("B+")),
                default=Value("B"),
            )),
            default=Value("C"),
        )
    """

    __slots__ = ("whens", "default")

    def __init__(self, *whens: When, default: Any | None = None):
        if not whens:
            raise ValueError(
                "Case() requires at least one When() clause. "
                "Example: Case(When(status='active', then=Value(1)), default=Value(0))"
            )
        invalid = [w for w in whens if not isinstance(w, When)]
        if invalid:
            raise TypeError(
                f"Case() only accepts When() instances as positional arguments. "
                f"Got: {[type(w).__name__ for w in invalid]}"
            )

        self.whens = whens
        self.default = default

    def resolve(
        self,
        field_resolver: FieldResolver,
        context: ResolverContext = ResolverContext(),
    ):
        """
        Resolve all When branches and the ELSE clause.

        Delegates condition parsing to the shared FieldResolver so that
        auto-joins, lookup validation, and F() resolution all work correctly.

        Returns:
            SQLAlchemy case() construct ready for use in SELECT/WHERE/ORDER BY.
        """
        resolved_whens = []
        for when in self.whens:
            try:
                condition, then = when.resolve(
                    field_resolver=field_resolver, context=context
                )
                resolved_whens.append((condition, then))
            except FilterError:
                raise
            except Exception as e:
                raise FilterError(
                    f"Failed to resolve When({when.condition!r}): {e}"
                ) from e

        resolved_default = None
        if self.default is not None:
            try:
                resolved_default = _resolve_expression(
                    expr=self.default, field_resolver=field_resolver, context=context
                )
            except FilterError:
                raise
            except Exception as e:
                raise FilterError(
                    f"Failed to resolve Case default={self.default!r}: {e}"
                ) from e

        logger.debug(
            "Resolved Case expression",
            branches=len(resolved_whens),
            has_default=resolved_default is not None,
        )

        return case(*resolved_whens, else_=resolved_default)

    def __repr__(self):
        whens_repr = ", ".join(repr(w) for w in self.whens)
        return f"Case({whens_repr}, default={self.default!r})"


def _resolve_expression(
    expr: Any, field_resolver: FieldResolver, context: ResolverContext
):
    """
    Resolve any supported expression type to a SQLAlchemy construct.

    Resolution order:
        1. Value(x)      -> literal(x)
        2. F("field")    -> resolved column (with auto-join)
        3. Case(...)     -> nested CASE expression
        4. Q(...)        -> boolean expression (for nested conditions)
        5. Safe scalars  -> literal(x) via auto-wrap
        6. Anything else -> TypeError

    Kept as a module-level function to avoid circular imports while
    remaining accessible to When and Case.
    """
    from sqlalchemy.sql.elements import ClauseElement

    from async_orm_kit.query.aggregations.base import Aggregate
    from async_orm_kit.query.q import Q

    if isinstance(expr, Aggregate):
        return expr.resolve(field_resolver=field_resolver, context=context)

    if isinstance(expr, CombinableMixin) and hasattr(expr, "resolve"):
        return expr.resolve(field_resolver=field_resolver, context=context)

    if isinstance(expr, ClauseElement):
        return expr

    if isinstance(expr, Q):
        return expr.resolve(field_resolver=field_resolver, context=context)

    # Auto-wrap only safe scalar types
    if isinstance(expr, SAFE_SCALAR_TYPES):
        return literal(expr)

    raise TypeError(
        f"Unsupported expression type in Case/When: {type(expr).__name__!r}. "
        f"Wrap it in Value(), F(), or use a Q() object."
    )
