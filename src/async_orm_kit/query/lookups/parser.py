from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from async_orm_kit.query.lookups.conditions import ConditionBuilder

from async_orm_kit.exceptions import FilterError, ModelAttributeError
from async_orm_kit.query.lookups.resolver import FieldResolver, ResolverContext


class FilterParser:
    """Parses filter kwargs into SQLAlchemy conditions through field resolution.

    FilterParser orchestrates the complete pipeline from raw filter kwargs to
    SQLAlchemy boolean expressions. It splits lookup-style keys (field__lookup),
    resolves them to database columns through FieldResolver, and builds conditions
    through ConditionBuilder. This separation of concerns keeps lookup parsing,
    field resolution, and condition building as independent, testable components.

    The parser handles the full transformation:
    {"status": "active", "price__gte": 100} → [status == 'active', price >= 100]

    It's used throughout the ORM for filter(), exclude(), and Case/When conditions,
    providing a consistent interface for all filter-style operations. The parser
    maintains context about OR blocks, negations, and filter marking to enable
    proper JOIN optimization and query planning.

    Single Responsibility: Own the complete kwargs-to-conditions pipeline without
    implementing field resolution (FieldResolver's job) or condition building
    (ConditionBuilder's job). This class is the coordinator, not the implementer.

    Args:
        field_resolver: FieldResolver instance for resolving field paths to columns
        and managing JOIN registration.

    Note:
        FilterParser uses lazy initialization for ConditionBuilder to avoid circular
        import issues at module load time. The builder is constructed on first use
        and cached for subsequent calls.
        The parser is stateless except for the cached ConditionBuilder - it can be
        reused across multiple parse() calls safely. Each parse() call operates
        independently with its own context.
    """

    def __init__(self, field_resolver: FieldResolver):
        """Initialize FilterParser with field resolver.

        Args:
            field_resolver: FieldResolver instance for resolving field paths and
                           managing JOIN registration across the query.
        """
        self._field_resolver = field_resolver
        # Lazily constructed — only allocated when parse() is first called
        self._condition_builder: ConditionBuilder | None = None

    def parse(
        self,
        kwargs: dict,
        *,
        context: ResolverContext = ResolverContext(mark_filter=True),
    ) -> list[Any]:
        """Parse filter kwargs dict into list of SQLAlchemy boolean expressions.

        Transforms a flat dictionary of filter conditions into SQLAlchemy clause
        elements by processing each key-value pair through the resolution pipeline.
        Each key is split into field path and lookup operator, resolved to a database
        column, and combined with the value to build a condition.

        The context parameter controls JOIN optimization behavior:
        - mark_filter=True: Marks JOINs as filter nodes, enabling INNER JOIN promotion
        - in_or_context=True: Prevents INNER JOIN promotion for OR block conditions
        - in_negation=True: Marks JOINs to stay as LEFT JOIN for NOT blocks

        Args:
            kwargs: Filter conditions as key-value pairs. Keys use Django-style
                   lookup syntax: "field__lookup" or "relation__field__lookup".
                   Values can be scalars, lists, F expressions, QuerySets, etc.
            context: ResolverContext controlling JOIN optimization and context
                    propagation. Defaults to mark_filter=True for standard filters.

        Returns:
            List of SQLAlchemy BinaryExpression or BooleanClauseList objects,
            one per kwargs entry, ready to be combined with AND/OR logic.

        Raises:
            FilterError: Lookup not supported, condition building failed, or
                        field resolution encountered an error.
            ModelAttributeError: Field path references non-existent field or
                                relationship on the model.

        Example:
            >>> # Standard filter parsing (mark_filter=True by default)
            >>> parser.parse({"status": "active", "price__gte": 100})
            >>> # Returns: [status == 'active', price >= 100]
            >>> # JOINs marked as filter nodes, eligible for INNER JOIN promotion
            >>>
            >>> # OR context parsing (prevents INNER JOIN optimization)
            >>> context = ResolverContext(in_or_context=True, mark_filter=True)
            >>> parser.parse({"category__name": "Books"}, context=context)
            >>> # Returns: [categories.name == 'Books']
            >>> # JOIN stays LEFT JOIN despite filter marking
            >>>
            >>> # Case/When parsing (no filter marking)
            >>> context = ResolverContext(mark_filter=False)
            >>> parser.parse({"stock__lte": 10}, context=context)
            >>> # Returns: [stock <= 10]
            >>> # JOIN not marked as filter, won't affect JOIN type
            >>>
            >>> # Negation context (NOT block)
            >>> context = ResolverContext(in_negation=True, mark_filter=True)
            >>> parser.parse({"supplier__active": True}, context=context)
            >>> # Returns: [suppliers.active == True]
            >>> # JOIN marked for negation, stays LEFT JOIN
            >>>
            >>> # Complex filters with F expressions
            >>> from async_orm_kit.query.expressions import F
            >>> parser.parse({"price__gt": F("cost") * 1.5})
            >>> # Returns: [price > cost * 1.5]
            >>>
            >>> # Relationship filters with subqueries
            >>> active_suppliers = Supplier.objects.filter(active=True)
            >>> parser.parse({
            ...     "supplier__in": active_suppliers.values_list("id", flat=True)
            ... })
            >>> # Returns: [supplier_id IN (SELECT id FROM suppliers WHERE ...)]

        Note:
            Performance is O(n) where n is the number of kwargs entries. Each entry
            requires field resolution (potentially O(m) for m-level relationship
            traversal) and condition building (O(1)).

            The context parameter is propagated through the entire resolution chain,
            ensuring consistent JOIN behavior across nested relationship traversals.
            This is critical for correct query optimization in complex scenarios.

            Empty kwargs dict returns empty list - this is valid and used internally
            for queries without filters.
        """
        if not isinstance(kwargs, dict):
            raise TypeError("FilterParser.parse() expects a dictionary of lookups.")
        if any(not isinstance(key, str) or not key for key in kwargs):
            raise TypeError("Filter lookup names must be non-empty strings.")

        conditions = []

        for key, value in kwargs.items():
            condition = self._parse_single(key=key, value=value, context=context)
            conditions.append(condition)

        return conditions

    def _parse_single(
        self,
        key: str,
        value: Any,
        *,
        context: ResolverContext = ResolverContext(),
    ) -> Any:
        """Resolve single key-value pair to SQLAlchemy condition.

        Implements the two-stage resolution pipeline:

        Stage 1: Field Resolution
        - Split key into field path and lookup operator
        - Traverse relationships and resolve to database column
        - Register JOINs with QueryBuilder
        - Return FieldResolution with column, lookup, and join_node

        Stage 2: Condition Building
        - Take resolved column, lookup, and value
        - Apply lookup operator to build SQLAlchemy expression
        - Handle special value types (F, QuerySet, Case, Value)
        - Return boolean clause element

        This separation ensures field resolution logic (relationship traversal,
        JOIN registration) stays independent from condition building logic
        (operator application, value resolution).

        Args:
            key: Filter key in Django-style lookup syntax. Examples: "status",
                "price__gte", "category__name__icontains".
            value: Filter value - scalar, list, F expression, QuerySet, etc.
            context: ResolverContext for JOIN optimization and context propagation.

        Returns:
            SQLAlchemy BinaryExpression or BooleanClauseList representing the
            filter condition.

        Raises:
            ModelAttributeError: Field path references non-existent attribute.
            FilterError: Field resolution or condition building failed.

        Example:
            >>> # Simple field filter
            >>> parser._parse_single("status", "active", context=context)
            >>> # Stage 1: Resolves "status" to products.status column
            >>> # Stage 2: Builds products.status == 'active'
            >>>
            >>> # Relationship filter with lookup
            >>> parser._parse_single("category__name__icontains", "elec", context=context)
            >>> # Stage 1: Traverses category relationship, resolves to categories.name
            >>> # Stage 2: Builds categories.name.ilike('%elec%')
            >>>
            >>> # F expression comparison
            >>> parser._parse_single("price__gt", F("cost") * 1.5, context=context)
            >>> # Stage 1: Resolves "price__gt" to products.price with __gt__ lookup
            >>> # Stage 2: Resolves F("cost") * 1.5, builds products.price > products.cost * 1.5

        Note:
            Error handling wraps both stages to provide clear error messages. Field
            resolution errors (ModelAttributeError, FilterError) are re-raised directly.
            Unexpected exceptions are wrapped in FilterError with context about which
            field failed.

            The ConditionBuilder is lazily initialized to avoid circular imports,
            but cached for performance across multiple calls.
        """
        try:
            resolution = self._field_resolver.resolve_to_column(
                key=key, context=context
            )
        except (ModelAttributeError, FilterError):
            raise
        except Exception as e:
            raise FilterError(f"Could not resolve field '{key}': {e}") from e

        from sqlalchemy.sql.elements import ColumnElement

        from async_orm_kit.query.lookups.json_paths import json_lookup

        builder = self._get_condition_builder()
        json_result = json_lookup(
            resolution.column,
            resolution.lookup,
            value,
            resolver=self._field_resolver,
            context=context,
        )
        if isinstance(json_result, ColumnElement):
            # A complete, dialect-guarded JSONB boolean predicate.
            return json_result
        column, lookup = (
            json_result
            if json_result is not None
            else (resolution.column, resolution.lookup)
        )
        return builder.build(
            column=column,
            lookup=lookup,
            value=value,
            join_node=resolution.join_node,
            context=context,
        )

    def _get_condition_builder(self) -> ConditionBuilder:
        """Lazy construction of ConditionBuilder to avoid circular import.ConditionBuilder imports FilterParser for type hints, and FilterParser
        imports ConditionBuilder for usage. Lazy initialization breaks this cycle
        by deferring the import until first use, after both modules are loaded.

        The builder is cached after first construction for performance - no need
        to recreate it on every parse() call.

        Returns:
            ConditionBuilder instance, either cached or newly constructed.

        Note:
            This pattern is common in Python for handling circular dependencies
            between closely related classes. The alternative would be to moveConditionBuilder to a separate module, but that would break the
            logical grouping of lookup-related classes.
        """
        if self._condition_builder is None:
            from async_orm_kit.query.lookups.conditions import ConditionBuilder

            self._condition_builder = ConditionBuilder(self._field_resolver)
        return self._condition_builder
