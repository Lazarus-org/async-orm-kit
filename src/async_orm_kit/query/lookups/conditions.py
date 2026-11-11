from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import ArgumentError

from async_orm_kit.exceptions import (
    FilterError,
    InvalidLookupError,
    InvalidRelationshipValueError,
)
from async_orm_kit.query.lookups import (
    FORBIDDEN_COLUMN_ATTRS,
    LOOKUP_OPERATORS,
    STANDARD_LOOKUPS,
)
from async_orm_kit.query.lookups.resolver.context import ResolverContext

if TYPE_CHECKING:
    from async_orm_kit.query.relations.node import JoinNode


class ConditionBuilder:
    """Builds SQLAlchemy filter conditions from resolved columns and lookup operators.

    ConditionBuilder is responsible for the final step of filter construction: taking
    a resolved column, a lookup operator, and a value, then producing a SQLAlchemy
    boolean expression. It handles custom lookup operators (in, range, isnull),
    standard SQLAlchemy column operators (==, !=, <, >, etc.), and special value
    types (F expressions, QuerySets, Case/Value).

    This class follows the Single Responsibility Principle by focusing solely on
    condition building. It does NOT handle field resolution (that's FieldResolver's
    job) or filter parsing (that's FilterParser's job). It receives already-resolved
    columns and produces ready-to-use SQLAlchemy clauses.

    The builder validates lookup operators before use, ensuring they're not private
    attributes, not in the forbidden list, exist on the column, are callable, and
    are valid SQLAlchemy operators. It also handles nullable checks for LEFT JOIN
    optimization and resolves special value types like F() and QuerySet subqueries.

    Args:
        field_resolver: Any instance for resolving F expressions and other
        special value types that reference database fields.

    Note:
        ConditionBuilder is typically used internally by FilterParser and QueryBuilder.
        Direct usage is rare unless you're building custom query compilation logic.

        The builder maintains a reference to FieldResolver to resolve F expressions
        and other field references that appear in filter values. This ensures consistent
        join tracking across the entire query.
    """

    def __init__(self, field_resolver: Any):
        """Initialize ConditionBuilder with field resolver.

        Args:
            field_resolver: Any instance for resolving F expressions and
                           field references in filter values.
        """
        self.field_resolver = field_resolver

    def build(
        self,
        column,
        lookup: str,
        value: Any,
        join_node: JoinNode | None = None,
        context: ResolverContext | None = None,
    ):
        """Build SQLAlchemy condition from resolved column, lookup, and value.
        Constructs a SQLAlchemy boolean expression by applying the lookup operator
        to the column with the given value. Handles three categories of lookups:

        1. Custom lookups (in, range, isnull, etc.) via LOOKUP_OPERATORS registry
        2. Standard SQLAlchemy operators (__eq__, __ne__, __lt__, __gt__, etc.)
        3. Special value types (F expressions, QuerySets, Case/Value)

        The method validates the lookup operator, resolves special value types,
        marks nullable checks for JOIN optimization, and handles relationship
        validation errors with helpful messages.

        Args:
            column: SQLAlchemy Column or InstrumentedAttribute to filter on.
            lookup: Lookup operator string (e.g., "__eq__", "in", "gte", "isnull").
            value: Filter value - can be scalar, list, F(), QuerySet, Case, Value, etc.
            join_node: Optional JoinNode for tracking nullable checks that affect
                      JOIN type selection (INNER vs LEFT).

        Returns:
            SQLAlchemy BinaryExpression or BooleanClauseList representing the filter
            condition, ready to be added to a WHERE clause.

        Raises:
            InvalidLookupError: Lookup operator is not supported, forbidden, or nota valid SQLAlchemy operator.
            InvalidRelationshipValueError: Value type is incompatible with relationshipfield (e.g., passing int instead of model
                                          instance).
            FilterError: General condition building failure, including SQLAlchemy
                        ArgumentError or unexpected exceptions.

        Note:
            Performance is O(1) for lookup validation and value resolution. The actual
            SQLAlchemy expression construction cost depends on the complexity of the
            value (e.g., nested F expressions or subqueries).Nullable checks (isnull=True)
            mark the join_node to prevent INNER JOIN
            optimization, ensuring NULL values are included in results. This is
            critical for correct LEFT JOIN behavior.
        """
        context = context or ResolverContext()
        self._mark_nullable_checks(join_node, lookup, value)
        value = self._resolve_value(value, context=context)

        try:
            # Application-installed lookups are per-QuerySet, never global.
            custom_lookups = self.field_resolver.query_state.custom_lookups
            if lookup in custom_lookups:
                from sqlalchemy.sql.elements import ColumnElement
                from sqlalchemy.sql.sqltypes import Boolean

                result = custom_lookups[lookup](column, value)
                if not isinstance(result, ColumnElement) or not isinstance(
                    result.type, Boolean
                ):
                    raise InvalidLookupError(
                        f"Custom lookup {lookup!r} must return a SQL boolean expression",
                        lookup=lookup,
                        field_name=getattr(column, "key", "<expression>"),
                    )
                return result

            # Built-in lookup operators (in, range, isnull, etc.)
            if lookup in LOOKUP_OPERATORS:
                return LOOKUP_OPERATORS[lookup](column, value)

            # Standard SQLAlchemy column operators (==, !=, <, >, etc.)
            self._validate_lookup(lookup, column)
            return getattr(column, lookup)(value)

        except (FilterError, InvalidLookupError, InvalidRelationshipValueError):
            raise

        except ArgumentError as e:
            if "mapped instance expected" in str(e).lower():
                column_name = getattr(
                    column, "key", getattr(column, "name", str(column))
                )
                raise InvalidRelationshipValueError(
                    f"Invalid value '{value!r}' for relationship '{column_name}' "
                    f"(lookup: '{lookup}'). Expected a mapped model instance, "
                    f"got {type(value).__name__}.",
                    value=value,
                    field_name=column_name,
                ) from e
            raise FilterError(
                f"Error building condition for lookup '{lookup}': {e}"
            ) from e

        except Exception as e:
            raise FilterError(
                f"Error building condition for lookup '{lookup}': {e}"
            ) from e

    def _resolve_value(
        self,
        value: Any,
        context: ResolverContext | None = None,
    ) -> Any:
        """Resolve special value types to SQLAlchemy expressions.

        Handles three categories of special values that require resolution before
        being used in filter conditions:

        1. F/FExpression - Field references that resolve to columns
        2. Value/Case - Literal wrappers and conditional expressions
        3. QuerySet - Subqueries that resolve to SELECT statements

        All other value types (scalars, lists, model instances, etc.) pass through
        unchanged. This method ensures that complex value types are properly converted
        to SQLAlchemy expressions that can be used in WHERE clauses.

        Args:
            value: Filter value to resolve. Can be F, FExpression, Value, Case,QuerySet, or any scalar/collection type.

        Returns:
            SQLAlchemy expression if value is a special type, otherwise the original
            value unchanged.

        Example:
            >>> # F expression resolution
            >>> f_expr = F("cost") * 1.5
            >>> resolved = builder._resolve_value(f_expr)
            >>> # Returns: products.cost * 1.5 (SQLAlchemy expression)
            >>>
            >>> # QuerySet subquery resolution
            >>> subquery = Product.objects.filter(category="electronics")
            >>> resolved = builder._resolve_value(subquery)
            >>> # Returns: SELECT ... FROM products WHERE category = 'electronics'
            >>>
            >>> # Scalar pass-through
            >>> resolved = builder._resolve_value(100)
            >>> # Returns: 100 (unchanged)
            >>>
            >>> # List pass-through
            >>> resolved = builder._resolve_value([1, 2, 3])
            >>> # Returns: [1, 2, 3] (unchanged)
        Note:
            Resolution is recursive for nested expressions. For example, an F expression
            containing another F expression will resolve both levels. QuerySet resolution
            triggers full query compilation, including joins and subquery optimization.
        """
        from async_orm_kit.query.expressions import Case, F, FExpression, Value
        from async_orm_kit.queryset.queryset import QuerySet

        context = context or ResolverContext()

        if isinstance(value, (F, FExpression)):
            return value.resolve(self.field_resolver, context=context)

        if isinstance(value, (Value, Case)):
            return value.resolve(self.field_resolver, context=context)

        from async_orm_kit.query.mixins.combinable import CombinableMixin

        if isinstance(value, CombinableMixin) and hasattr(value, "resolve"):
            return value.resolve(self.field_resolver, context=context)

        if isinstance(value, QuerySet):
            from sqlalchemy import inspect as sa_inspect

            from async_orm_kit.exceptions import QueryStateError

            state = value._state.clone()
            state.select_related.clear()
            state.prefetch_related.clear()
            if state.values_select is not None:
                if len(state.values_select) != 1 or state.annotations:
                    raise QueryStateError("Subquery must select exactly one column")
                column = state.values_select[0][1]
            elif state.values_list_select is not None:
                if len(state.values_list_select) != 1 or state.annotations:
                    raise QueryStateError("Subquery must select exactly one column")
                column = state.values_list_select[0][1]
            else:
                if state.annotations or state.has_aggregates:
                    raise QueryStateError(
                        "Annotated subqueries require an explicit single-column projection"
                    )
                pk = sa_inspect(value.model).primary_key
                if len(pk) != 1:
                    raise QueryStateError(
                        "Composite-key subqueries require an explicit single-column projection"
                    )
                column = getattr(value.model, pk[0].key)
            compiled = value._compiler.compile(state)
            return compiled.with_only_columns(
                column, maintain_column_froms=True
            ).order_by(None)

        return value

    @staticmethod
    def _mark_nullable_checks(join_node, lookup: str, value: Any):
        """Mark join node if nullable check is used to prevent INNER JOIN optimization.

        When filtering with isnull=True, the query must use LEFT JOIN instead of
        INNER JOIN to include rows where the relationship is NULL. This method marks
        the join_node to signal the JOIN builder to use LEFT JOIN.

        Args:
            join_node: JoinNode to mark, or None if no join is involved.
            lookup: Lookup operator string.
            value: Filter value.

        Example:
            >>> # Without marking: INNER JOIN (excludes NULL relationships)
            >>> Order.objects.filter(customer__name="John")
            >>> # SELECT * FROM orders INNER JOIN customers ON ...
            >>>
            >>> # With marking: LEFT JOIN (includes NULL relationships)
            >>> Order.objects.filter(customer__isnull=True)
            >>> # SELECT * FROM orders LEFT JOIN customers ON ... WHERE customers.id IS NULL

        Note:
            This is a critical optimization for correct NULL handling. Without this
            marking, isnull=True filters would incorrectly use INNER JOIN and return
            empty results.
        """
        if join_node and lookup == "isnull" and value is True:
            join_node.has_nullable_check = True

    @staticmethod
    def _validate_lookup(lookup: str, column):
        """Allow only explicitly supported boolean SQLAlchemy operators."""
        column_name = getattr(column, "key", getattr(column, "name", "<expression>"))

        if (
            not isinstance(lookup, str)
            or lookup.startswith("_")
            or lookup in FORBIDDEN_COLUMN_ATTRS
            or lookup not in STANDARD_LOOKUPS
        ):
            raise InvalidLookupError(
                f"Unsupported lookup '{lookup}' for field '{column_name}'",
                lookup=lookup,
                field_name=column_name,
            )

        attr = getattr(column, lookup, None)
        if not callable(attr):
            raise InvalidLookupError(
                f"Unsupported lookup '{lookup}' for field '{column_name}'",
                lookup=lookup,
                field_name=column_name,
            )
