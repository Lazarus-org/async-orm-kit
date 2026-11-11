from typing import Any

from sqlalchemy import and_, true

from async_orm_kit._logging import get_logger
from async_orm_kit.exceptions import (
    ExpressionTypeError,
    FilterError,
    ModelAttributeError,
    QueryStateError,
    RelationshipError,
)
from async_orm_kit.query.aggregations import Aggregate
from async_orm_kit.query.expressions.f import F, FExpression
from async_orm_kit.query.lookups.conditions import ConditionBuilder
from async_orm_kit.query.lookups.parser import FilterParser
from async_orm_kit.query.lookups.resolver import FieldResolver, ResolverContext
from async_orm_kit.query.mixins.orderable import OrderedExpression
from async_orm_kit.query.q import Q
from async_orm_kit.query.relations.graph import JoinGraph

logger = get_logger(__name__)


class QueryBuilder:
    """Constructs SQL queries by transforming QuerySet operations into SQLAlchemy expressions.

    Translates high-level QuerySet operations (filter, annotate, order_by, etc.) into
    SQLAlchemy query components. Manages join planning through JoinGraph and delegates
    field resolution and condition building to specialized components. Modifies QueryState
    in place as operations are applied.

    This class bridges the gap between Django-style ORM syntax and SQLAlchemy's expression
    language, handling:
    - Filter/exclude condition parsing with Q objects and lookups
    - Relationship traversal and automatic join creation
    - Expression resolution (F, Case, Value, aggregates)
    - Query projections (values, values_list, only)
    - Ordering with support for annotations and expressionsAttributes:
        _state: QueryState instance being modified by operations.
        _field_resolver: Resolves field paths to SQLAlchemy columns and manages joins.
        _condition_builder: Builds SQLAlchemy conditions from lookup expressions.

    Example:
        >>> # QueryBuilder is internal - users interact via QuerySet
        >>> from your_app.models import DBUser
        >>> qs = DBUser.objects.filter(active=True)
        >>>
        >>> # Under the hood, QuerySet delegates to QueryBuilder
        >>> builder = QueryBuilder(qs._state)
        >>> builder.filter(role="admin")
        >>> builder.order_by("-created_at")
        >>> builder.limit(10)
        >>>
        >>> # State is modified in place
        >>> print(qs._state.filters)  # [BinaryExpression(...)]
        >>> print(qs._state.order_by)  # [UnaryExpression(...)]

    Note:
        Performance: Operations are O(1) to O(n) where n is the number of fields/conditions
        in the operation. Join planning is lazy - joins are only created when fields are
        resolved. State modifications are in-place for efficiency.
    """

    def __init__(self, state):
        """Initialize QueryBuilder with state and helper components.

        Sets up field resolver and condition builder, initializing JoinGraph if needed.

        Args:
            state: QueryState instance to modify with query operations.
        """
        self._state = state

        if self._state.join_graph is None:
            self._state.join_graph = JoinGraph(self._state.model)

        # Initialize helper components
        self._field_resolver = FieldResolver(self._state)
        self._condition_builder = ConditionBuilder(self._field_resolver)

    def _lookup_phase(self, field: str, value: Any) -> str:
        """Identify the SQL clause for a lookup without changing query state.

        Both sides matter: ``score__gt=F('total')`` references an aggregate
        even though ``score`` itself is not an annotation. A scratch clone
        resolves expression-valued RHS operands without registering their JOINs
        on the QuerySet being classified.
        """
        from sqlalchemy.sql.elements import ClauseElement

        from async_orm_kit.query.expressions.analysis import (
            contains_aggregate,
            contains_window,
        )
        from async_orm_kit.query.mixins.combinable import CombinableMixin

        annotation = field.split("__", 1)[0]
        left = self._state.annotations.get(annotation)
        if left is not None and contains_window(left):
            raise FilterError(
                "Window annotations cannot be filtered directly; "
                "wrap the query in a subquery"
            )
        is_aggregate = left is not None and contains_aggregate(left)

        if isinstance(value, (CombinableMixin, ClauseElement)):
            scratch = QueryBuilder(self._state.clone())
            right, _ = scratch._resolve_expression(
                value, ResolverContext(mark_filter=False)
            )
            if contains_window(right):
                raise FilterError(
                    "Window expressions cannot be filtered directly; "
                    "wrap the query in a subquery"
                )
            is_aggregate = is_aggregate or contains_aggregate(right)

        if is_aggregate and not self._state.has_aggregates:
            raise FilterError(
                "Aggregate predicates require an annotated, grouped QuerySet"
            )
        return "having" if is_aggregate else "where"

    def _q_predicate_phases(self, predicate: Q) -> set[str]:
        """Classify Q leaves without mutating the production join graph.

        Conjunctive predicates can be split into WHERE and HAVING; an OR/NOT
        spanning both clauses is rejected rather than silently reinterpreted.
        """
        phases: set[str] = set()
        for child in predicate.children:
            if isinstance(child, Q):
                phases.update(self._q_predicate_phases(child))
            else:
                _, field, value = child
                phases.add(self._lookup_phase(field, value))
        return phases

    def _append_q_predicate(
        self, predicate: Q, context: ResolverContext, *, only_having: bool = False
    ) -> None:
        phases = self._q_predicate_phases(predicate)
        if not phases:
            return
        if only_having and "where" in phases:
            raise FilterError(
                "having() Q predicates must reference aggregate annotations"
            )
        if len(phases) == 1:
            condition = predicate.resolve(self._field_resolver, context=context)
            target = (
                self._state.having_conditions
                if "having" in phases
                else self._state.filters
            )
            target.append(condition)
            return

        if predicate.negated or predicate.connector != Q.AND:
            condition = self._resolve_mixed_group_predicate(predicate)
            self._state.having_conditions.append(condition)
            return
        for child in predicate.children:
            if isinstance(child, Q):
                self._append_q_predicate(child, context, only_having=only_having)
            else:
                _, field, value = child
                self._append_q_predicate(
                    Q(**{field: value}), context, only_having=only_having
                )

    def _resolve_mixed_group_predicate(self, predicate: Q):
        """Resolve mixed Q trees in HAVING without changing aggregate inputs.

        The row-only part is evaluated in a correlated EXISTS subquery whose
        root entity is a distinct SQLAlchemy alias. This preserves same-row
        AND semantics without registering additional relationships on the
        aggregate SELECT (which could otherwise inflate COUNT/SUM results).
        SQL row-level and grouped predicates can then combine safely in HAVING.
        """
        from sqlalchemy import and_, inspect as sa_inspect, literal, or_
        from sqlalchemy.orm import aliased

        from async_orm_kit.query.compiler import QueryCompiler
        from async_orm_kit.query.lookups.parser import FilterParser
        from async_orm_kit.queryset.state import QueryState

        context = ResolverContext(
            mark_filter=False, in_or_context=True, in_negation=True
        )

        def phases_of(child):
            if isinstance(child, Q):
                return self._q_predicate_phases(child)
            return {self._lookup_phase(child[1], child[2])}

        def group_row_children(children, connector):
            if not children:
                return None
            # Keep the complete AND/OR row predicate in ONE inner SELECT.
            # An outer row JOIN would multiply values in the aggregate SELECT.
            grouped = Q._from_parts(tuple(children), connector, False)
            inner_model = aliased(self._state.model)
            inner_state = QueryState(model=inner_model)
            QueryBuilder(inner_state).filter(grouped)
            statement = QueryCompiler().compile(inner_state)
            mapper = sa_inspect(self._state.model)
            mapper = mapper.mapper if mapper.is_aliased_class else mapper
            identity = and_(
                *(
                    getattr(inner_model, col.key) == getattr(self._state.model, col.key)
                    for col in mapper.primary_key
                )
            )
            statement = statement.with_only_columns(
                literal(1), maintain_column_froms=True
            ).where(identity)
            return statement.correlate(self._state.model).exists()

        def build(q):
            branches = []
            row_children = []
            for child in q.children:
                phases = phases_of(child)
                if phases == {"where"}:
                    row_children.append(child)
                elif isinstance(child, Q):
                    branches.append(build(child))
                else:
                    _, key, value = child
                    branches.append(
                        self._resolve_having_condition(key, value, context)
                        if key.split("__", 1)[0] in self._state.annotations
                        else FilterParser(self._field_resolver).parse(
                            {key: value}, context=context
                        )[0]
                    )
            row_branch = group_row_children(row_children, q.connector)
            if row_branch is not None:
                branches.insert(0, row_branch)
            if not branches:
                return None
            result = or_(*branches) if q.connector == Q.OR else and_(*branches)
            return result.is_not(True) if q.negated else result

        if not self._state.has_aggregates:
            raise FilterError("Mixed Q grouping requires an aggregate annotation")
        return build(predicate)

    def filter(self, *args, **kwargs):
        """Add WHERE conditions to restrict query results.

        Parses filter expressions and converts them to SQLAlchemy conditions. Supports
        Q objects for complex logic (AND/OR/NOT) and keyword lookups for simple filters.
        Multiple conditions are combined with AND. Automatically creates joins for
        relationship traversal.

        Args:
            *args: Q objects for complex filter logic.
            **kwargs: Field lookups in format field__lookup=value (e.g., age__gte=18).

        Returns:
            Modified QueryState with new filter conditions appended.

        Raises:
            TypeError: If positional args are not Q objects.
            ModelAttributeError: If field doesn't exist on model.
            RelationshipError: If relationship path is invalid.
            FilterError: If lookup is invalid or expression fails to resolve.

        Example:
            >>> # Simple filters
            >>> builder.filter(active=True, role="admin")

            >>> # Lookups
            >>> builder.filter(age__gte=18, created_at__year=2024)

            >>> # Relationship traversal
            >>> builder.filter(author__username="john")

            >>> # Q objects for complex logic
            >>> builder.filter(Q(active=True) | Q(role="admin"))
            >>> builder.filter(Q(age__gte=18), role="user")  # AND between Q and kwargs

        Note:
            Performance: O(n) where n is number of conditions. Each relationship traversal
            may create joins. Conditions are evaluated lazily - no database query until
            QuerySet is executed.
        """
        if not args and not kwargs:
            return self._state

        try:
            from async_orm_kit.query.expressions.analysis import (
                contains_aggregate,
                contains_window,
            )

            context = ResolverContext(mark_filter=True)
            for arg in args:
                from sqlalchemy.sql.sqltypes import Boolean

                from async_orm_kit.query.mixins.combinable import CombinableMixin

                if isinstance(arg, CombinableMixin) and hasattr(arg, "resolve"):
                    expression = arg.resolve(self._field_resolver, context)
                    if not isinstance(expression.type, Boolean):
                        raise TypeError(
                            "filter() expression arguments must resolve to SQL boolean predicates"
                        )
                    if contains_window(expression):
                        raise FilterError(
                            "Window predicates cannot be used in WHERE/HAVING directly; use a subquery"
                        )
                    if contains_aggregate(expression):
                        self._state.having_conditions.append(expression)
                    else:
                        self._state.filters.append(expression)
                    continue
                if not isinstance(arg, Q):
                    raise TypeError(
                        "filter() positional arguments must be Q or SQL predicate "
                        f"expressions, not {type(arg).__name__}"
                    )
                self._append_q_predicate(arg, context)

            if kwargs:
                parser = FilterParser(self._field_resolver)
                for key, value in kwargs.items():
                    phase = self._lookup_phase(key, value)
                    annotation = key.split("__", 1)[0]
                    if phase == "having" and annotation in self._state.annotations:
                        from async_orm_kit.query.expressions.analysis import (
                            contains_aggregate,
                        )

                        if contains_aggregate(self._state.annotations[annotation]):
                            condition = self._resolve_having_condition(
                                key, value, context
                            )
                            self._state.having_conditions.append(condition)
                            continue
                    conditions = parser.parse({key: value}, context=context)
                    target = (
                        self._state.having_conditions
                        if phase == "having"
                        else self._state.filters
                    )
                    target.extend(conditions)

            return self._state

        except (
            ModelAttributeError,
            RelationshipError,
            FilterError,
            QueryStateError,
            TypeError,
        ):
            raise
        except Exception as e:
            logger.error("Filter failed")
            raise FilterError(f"Unexpected error in filter: {str(e)}") from e

    def exclude(self, *args, **kwargs):
        """Add negated WHERE conditions to exclude matching rows.

        Excludes rows for which the combined predicate evaluates to TRUE. Rows whose
        nullable predicates evaluate to SQL UNKNOWN are retained, matching the intent
        of excluding only records that actually satisfy the conditions.

        Args:
            *args: Q objects for complex exclusion logic.
            **kwargs: Field lookups to negate.

        Returns:
            Modified QueryState with negated filter condition appended.

        Raises:
            TypeError: If positional args are not Q objects.
            ModelAttributeError: If field doesn't exist on model.
            RelationshipError: If relationship path is invalid.
            FilterError: If lookup is invalid or expression fails to resolve.

        Example:
            >>> # Exclude simple condition
            >>> builder.exclude(status="deleted")
            >>> # SQL: WHERE (status = 'deleted') IS NOT TRUE

            >>> # Exclude with Q objects
            >>> builder.exclude(Q(status="deleted") | Q(archived=True))
            >>> # SQL: WHERE ((status = 'deleted') OR (archived = true)) IS NOT TRUE

            >>> # Multiple conditions (AND)
            >>> builder.exclude(active=False, role="guest")
            >>> # SQL: WHERE ((active = false) AND (role = 'guest')) IS NOT TRUE

            >>> # Relationship traversal
            >>> builder.exclude(author__username="spam_user")

        Note:
            Performance: Same as filter(). All conditions are combined with AND before
            exclusion. `IS NOT TRUE` retains rows where a nullable predicate evaluates
            to UNKNOWN instead of accidentally dropping them through SQL three-valued
            logic. Relationship paths are kept as LEFT JOINs while compiling exclusions.
        """
        if not args and not kwargs:
            return self._state

        try:
            from async_orm_kit.query.lookups.correlated import resolve_q

            context = ResolverContext(mark_filter=True, in_negation=True)
            from sqlalchemy.sql.sqltypes import Boolean

            from async_orm_kit.query.expressions.analysis import (
                contains_aggregate,
                contains_window,
            )
            from async_orm_kit.query.mixins.combinable import CombinableMixin

            predicates = []
            q_args = []
            for arg in args:
                if isinstance(arg, Q):
                    q_args.append(arg)
                elif isinstance(arg, CombinableMixin) and hasattr(arg, "resolve"):
                    expression = arg.resolve(self._field_resolver, context)
                    if not isinstance(expression.type, Boolean):
                        raise TypeError(
                            "exclude() expression arguments must resolve to SQL boolean predicates"
                        )
                    if contains_window(expression) or contains_aggregate(expression):
                        raise FilterError(
                            "Aggregate/window exclusion predicates require an explicit subquery"
                        )
                    predicates.append(expression)
                else:
                    raise TypeError(
                        "exclude() positional arguments must be Q or SQL predicate "
                        f"expressions, not {type(arg).__name__}"
                    )

            if q_args or kwargs:
                grouped = Q(*q_args, **kwargs)
                phases = self._q_predicate_phases(grouped)
                if "having" in phases:
                    if predicates:
                        raise FilterError(
                            "Combining aggregate Q exclusions with raw predicates "
                            "requires an explicit grouped subquery"
                        )
                    if "where" in phases:
                        condition = self._resolve_mixed_group_predicate(grouped)
                    else:
                        condition = grouped.resolve(
                            self._field_resolver,
                            context=ResolverContext(
                                mark_filter=False, in_negation=True
                            ),
                        )
                    self._state.having_conditions.append(condition.is_not(true()))
                    return self._state
                condition = resolve_q(grouped, self._field_resolver, context)
                if condition is not None:
                    predicates.append(condition)
            if predicates:
                self._state.filters.append(and_(*predicates).is_not(true()))
            return self._state

        except (
            ModelAttributeError,
            RelationshipError,
            FilterError,
            QueryStateError,
            TypeError,
        ):
            raise
        except Exception as e:
            logger.error("Exclude failed")
            raise FilterError(f"Error in exclude: {str(e)}") from e

    def order_by(self, *fields):
        """Set ORDER BY clause for result sorting.

        Replaces any existing ordering. Supports string field names with "-" prefix for
        descending order, relationship traversal, annotated fields, and expression objects
        with .asc()/.desc() methods. Multiple fields are applied in order.

        Args:
            *fields: Field names (str) or OrderedExpression instances. Strings support"-" prefix for DESC and "__" for relationships. Expressions must
                    call .asc() or .desc() to be orderable.

        Returns:
            Modified QueryState with order_by list replaced.

        Raises:
            ExpressionTypeError: If expression doesn't have .asc()/.desc() methods.
            ModelAttributeError: If field doesn't exist on model.
            RelationshipError: If relationship path is invalid.
            FilterError: If ordering fails unexpectedly.

        Example:
            >>> # Simple ordering
            >>> builder.order_by("created_at")  # ASC
            >>> builder.order_by("-created_at")  # DESC

            >>> # Multiple fields
            >>> builder.order_by("status", "-created_at")

            >>> # Relationship traversal
            >>> builder.order_by("author__name", "-author__created_at")

            >>> # Annotated fields
            >>> builder.annotate(total=Sum("items__price"))
            >>> builder.order_by("-total")

            >>> # Expression objects
            >>> builder.order_by((F("price") * F("quantity")).desc())
            >>> builder.order_by(Case(When(vip=True, then=0), default=1).asc())

        Note:
            Performance: O(n) where n is number of fields. Clears previous ordering.
            Expressions like F() * F() must call .asc() or .desc() to become orderable.
            Annotated fields are referenced by alias name as strings.
        """
        if not fields:
            return self._state

        if self._state.seek_active:
            raise QueryStateError(
                "Cannot change ordering after seek_after()/seek_before()"
            )

        # Clear previous ordering
        self._state.order_by.clear()
        self._state.ordering_fields = (
            list(fields) if all(isinstance(field, str) for field in fields) else []
        )

        try:
            from sqlalchemy import asc, desc

            context = ResolverContext(in_order_by=True)

            for f in fields:
                if isinstance(f, OrderedExpression):
                    resolved = f.resolve(
                        field_resolver=self._field_resolver, context=context
                    )
                    self._state.order_by.append(resolved)
                    continue

                # ── String reference: "name", "-created_at" ──
                if not isinstance(f, str):
                    raise ExpressionTypeError(
                        f"order_by() accepts strings or OrderedExpression instances, "
                        f"got {type(f).__name__}. "
                        f"If you are using expressions such as F(), Value(), "
                        f"Case(), or arithmetic combinations thereof, "
                        f"you must call .asc() or .desc() on them to produce "
                        f"an orderable expression — e.g. "
                        f"order_by((F('price') * F('qty')).desc())."
                    )

                if not f or f == "-" or f.startswith("--"):
                    raise QueryStateError(
                        f"Invalid order_by field {f!r}; use a field name with at most "
                        "one leading '-' for descending order."
                    )

                descending = f.startswith("-")
                field_name = f[1:] if descending else f

                # Use the already-resolved annotation expression rather than
                # interpolating a caller-controlled alias into SQL text.
                if field_name in self._state.annotations:
                    expression = self._state.annotations[field_name]
                    column = desc(expression) if descending else asc(expression)
                else:
                    resolution = self._field_resolver.resolve_to_column(
                        key=field_name, context=context
                    )
                    column = (
                        resolution.column.desc()
                        if descending
                        else resolution.column.asc()
                    )

                self._state.order_by.append(column)

            return self._state

        except (
            ModelAttributeError,
            RelationshipError,
            ExpressionTypeError,
            QueryStateError,
        ):
            raise
        except Exception as e:
            logger.error("order_by failed")
            raise FilterError(f"Error in order_by: {str(e)}") from e

    def limit(self, limit: int):
        """Set maximum number of rows to return (LIMIT clause).

        Args:
            limit: Maximum number of rows. Must be non-negative integer.

        Returns:
            Modified QueryState with limit_val set.

        Raises:
            ValueError: If limit is negative or not an integer.

        Example:
            >>> builder.limit(10)  # Return at most 10 rows
            >>> builder.limit(0)   # Return no rows (valid but unusual)

        Note:
            Performance: O(1). Limit is applied at database level - efficient for
            pagination. Combine with offset() for pagination.
        """
        if type(limit) is not int or limit < 0:
            raise ValueError(f"Limit must be a non-negative integer, got {limit}")
        self._state.limit_val = limit
        return self._state

    def offset(self, offset: int):
        """Set number of rows to skip (OFFSET clause).

        Args:
            offset: Number of rows to skip. Must be non-negative integer.

        Returns:
            Modified QueryState with offset_val set.

        Raises:
            ValueError: If offset is negative or not an integer.

        Example:
            >>> builder.offset(20)  # Skip first 20 rows
            >>> builder.limit(10).offset(20)  # Rows 21-30 (pagination)

        Note:
            Performance: O(1) operation but O(n) at database level where n is offset.
            Large offsets are slow - database must scan and discard rows. Consider
            cursor-based pagination for large datasets.
        """
        if type(offset) is not int or offset < 0:
            raise ValueError(f"Offset must be a non-negative integer, got {offset}")

        if self._state.seek_active:
            raise QueryStateError("OFFSET cannot be combined with keyset pagination")
        self._state.offset_val = offset
        return self._state

    def select_related(self, *relations: str):
        """Configure joined eager loading for scalar (to-one) relationships.

        Every segment of a nested path must be many-to-one or one-to-one.
        Collection relationships belong in ``prefetch_related()`` so the root
        result set is not multiplied.
        """
        for path in relations:
            relationship_path = self._state.join_graph.require_relationship_path(path)
            for segment, relationship in zip(
                path.split("__"), relationship_path, strict=False
            ):
                if relationship.uselist:
                    raise RelationshipError(
                        f"select_related({path!r}) crosses collection relationship "
                        f"{segment!r}; use prefetch_related() for collection paths."
                    )
            if path not in self._state.select_related:
                self._state.select_related.append(path)
            # Exact-path select_related takes precedence over prefetch_related.
            if path in self._state.prefetch_related:
                self._state.prefetch_related.remove(path)
        return self._state

    def prefetch_related(self, *relations: str):
        """Configure select-in eager loading for scalar or collection paths."""
        for path in relations:
            self._state.join_graph.require_relationship_path(path)
            if path not in self._state.prefetch_related:
                self._state.prefetch_related.append(path)
        # select_related has precedence when both APIs name the same path.
        self._state.prefetch_related = [
            path
            for path in self._state.prefetch_related
            if path not in self._state.select_related
        ]
        return self._state

    def annotate(self, **kwargs):
        """Add computed fields to query results (aggregates, expressions).

        Creates new fields by evaluating expressions or aggregates. Annotated fields
        can be used in filters, ordering, and projections. Aggregate annotations
        must trigger GROUP BY using .values().

        Args:
            **kwargs: Alias=expression pairs. Expressions can be F(), Case(), Value(),
                     or aggregate functions (Count, Sum, Avg, etc.).

        Returns:
            Modified QueryState with annotations added.

        Raises:
            ModelAttributeError: If field in expression doesn't exist.
            RelationshipError: If relationship path is invalid.

        Example:
            >>> # Aggregate annotations
            >>> builder.annotate(post_count=Count("posts"))
            >>> builder.annotate(total_price=Sum("items__price"))
            >>> # Expression annotations
            >>> builder.annotate(full_price=F("price") * F("quantity"))
            >>> builder.annotate(
            ...     discount_price=Case(
            ...         When(vip=True, then=F("price") * 0.8),
            ...         default=F("price")
            ...     )
            ... )

            >>> # Use in filters and ordering
            >>> builder.annotate(total=Sum("items__price"))
            >>> builder.filter(total__gte=100)>>> builder.order_by("-total")

        Note:
            Performance: Aggregates trigger GROUP BY which can be expensive. Expressions
            are computed per row. Annotated fields are available in Python and SQL.
            Relationship traversal in annotations creates joins automatically.
        """
        import keyword

        from sqlalchemy import inspect as sa_inspect

        context = ResolverContext(in_annotation=True)
        inspected_model = sa_inspect(self._state.model)
        mapper = (
            inspected_model.mapper
            if inspected_model.is_aliased_class
            else inspected_model
        )
        mapped_names = set(mapper.attrs.keys())
        for alias, expr in kwargs.items():
            if (
                not isinstance(alias, str)
                or not alias.isidentifier()
                or keyword.iskeyword(alias)
                or alias.startswith("_")
            ):
                raise ValueError(
                    f"Annotation alias {alias!r} must be a public Python identifier."
                )
            if alias in mapped_names and alias not in self._state.annotations:
                raise ValueError(
                    f"Annotation alias {alias!r} conflicts with a mapped model attribute."
                )

            sql_expr, is_aggregate = self._resolve_expression(
                expr=expr, context=context
            )
            if is_aggregate and not self._state.has_aggregates:
                # Freeze the grouping identity when the first aggregate is
                # introduced. A subsequent values()/values_list() projection may
                # select ONLY an aggregate; grouping by that aggregate is invalid
                # SQL, and dropping the original group makes a scalar subquery
                # aggregate across unrelated parent identities.
                if self._state.values_select is not None:
                    group_columns = [col for _, col in self._state.values_select]
                elif self._state.values_list_select is not None:
                    group_columns = [col for _, col in self._state.values_list_select]
                else:
                    from sqlalchemy import inspect as sa_inspect

                    group_columns = [
                        getattr(self._state.model, attr.key)
                        for attr in mapper.column_attrs
                    ]
                self._state.group_by_fields = group_columns
            self._state.annotations[alias] = sql_expr
            # Store which paths this annotation depends on
            self._state.annotation_paths[alias] = (
                self._field_resolver.pop_resolved_paths()
            )
            if is_aggregate:
                self._state.has_aggregates = True

        return self._state

    def having(self, *conditions: Q, **lookups):
        """Add HAVING conditions to filter aggregated results.

        Filters rows after GROUP BY aggregation. Similar to filter() but operates on
        aggregate values. Requires annotate() to create aggregate fields first.

        Args:
            **lookups: Annotation lookups in format annotation__lookup=value.
                      Annotation name must match an annotate() alias.

        Returns:
            Modified QueryState with HAVING conditions appended.

        Raises:
            FilterError: If annotation name doesn't exist or lookup is invalid.

        Example:
            >>> # Filter by aggregate value
            >>> builder.annotate(post_count=Count("posts"))
            >>> builder.having(post_count__gte=5)
            >>> # SQL: HAVING COUNT(posts.id) >= 5

            >>> # Multiple conditions
            >>> builder.annotate(total=Sum("items__price"), count=Count("items"))
            >>> builder.having(total__gte=100, count__gte=3)

            >>> # Complex annotation names
            >>> builder.annotate(order__total=Sum("order__items__price"))
            >>> builder.having(order__total__gte=1000)

        Note:
            Performance: HAVING is evaluated after GROUP BY - more efficient than
            filtering aggregated results in Python. Use filter() for pre-aggregation
            filtering when possible.
        """
        if not conditions and not lookups:
            return self._state

        context = ResolverContext(mark_filter=True)
        for predicate in conditions:
            if not isinstance(predicate, Q):
                raise TypeError("having() positional conditions must be Q objects")
            self._append_q_predicate(predicate, context, only_having=True)

        for raw_key, value in lookups.items():
            condition = self._resolve_having_condition(
                raw_key=raw_key, value=value, context=context
            )
            self._state.having_conditions.append(condition)

        return self._state

    def distinct(self, *fields):
        """Remove duplicate rows from results (DISTINCT clause).

        Without arguments, applies SELECT DISTINCT to remove duplicate rows across all
        columns. With arguments (PostgreSQL only), applies DISTINCT ON to remove duplicates
        based on specific fields, keeping first occurrence per group.

        Args:
            *fields: Optional field names for DISTINCT ON (PostgreSQL only).    Supports relationship traversal with "__".

        Returns:
            Modified QueryState with distinct configuration set.

        Raises:
            ModelAttributeError: If field doesn't exist.
            RelationshipError: If relationship path is invalid.

        Example:
            >>> # Remove all duplicates
            >>> builder.distinct()
            >>> # SQL: SELECT DISTINCT * FROM users

            >>> # PostgreSQL DISTINCT ON
            >>> builder.order_by("customer_id", "-created_at")
            >>> builder.distinct("customer_id")
            >>> # Returns most recent order per customer

            >>> # Relationship traversal
            >>> builder.distinct("author__username")

        Note:
            Performance: DISTINCT requires sorting/hashing - O(n log n). Expensive on
            large result sets. DISTINCT ON (PostgreSQL) requires ORDER BY to include
            distinct fields first. Consider filtering instead if possible.
        """
        if fields:
            context = ResolverContext()
            # Resolve each field to trigger necessary joins
            resolved_fields = []
            for field in fields:
                resolution = self._field_resolver.resolve_to_column(
                    key=field, context=context
                )
                resolved_fields.append(resolution.column)

            self._state.distinct_on = resolved_fields
            self._state.is_distinct = False
        else:
            self._state.distinct_on = []
            self._state.is_distinct = True

        return self._state

    def defer(self, *fields: str):
        """Defer mapped scalar columns on the root model for model-result queries.

        SQLAlchemy async sessions do not support implicit attribute I/O: load a
        deferred value explicitly using ``await session.refresh(instance, [name])``.
        Relationship paths, primary keys, and non-column attributes are rejected.
        """
        from sqlalchemy import inspect as sa_inspect

        from async_orm_kit.exceptions import ModelAttributeError, QueryStateError

        mapper_info = sa_inspect(self._state.model)
        mapper = mapper_info.mapper if mapper_info.is_aliased_class else mapper_info
        primary_keys = {col.key for col in mapper.primary_key}
        for name in fields:
            if (
                not isinstance(name, str)
                or not name.isidentifier()
                or name.startswith("_")
                or "__" in name
            ):
                raise ValueError("defer() requires plain root-model column names")
            if name in primary_keys:
                raise QueryStateError("Primary-key columns cannot be deferred")
            if name not in mapper.column_attrs:
                raise ModelAttributeError(f"Unknown scalar field for defer(): {name!r}")
            if name not in self._state.deferred_fields:
                self._state.deferred_fields.append(name)
        return self._state

    def only(self, *fields):
        """Load only specified fields, deferring others (deferred loading).

        Optimizes queries by loading only required fields. Deferred fields trigger
        additional queries when accessed. Returns model instances, not dicts. Useful
        for models with large text/binary fields.

        Args:
            *fields: Field names to load immediately. Supports relationship traversal
                    with "__" for select_related fields.

        Returns:
            Modified QueryState with only_fields set and values modes cleared.

        Raises:
            ModelAttributeError: If field doesn't exist.
            RelationshipError: If relationship path is invalid.

        Example:
            >>> # Load only specific fields
            >>> builder.only("id", "username")
            >>> # Other fields trigger queries on access

            >>> # Relationship fields
            >>> builder.select_related("author")
            >>> builder.only("title", "author__username")

            >>> # Useful for large fields
            >>> builder.only("id", "title")  # Skip large 'content' field

        Note:
            Performance: Reduces initial query size and memory. However, accessing
            deferred fields triggers N additional queries. Use when certain fields
            won't be accessed. For read-only projections, use values() instead.
            Primary key is always loaded.
        """
        if not fields:
            return self._state

        resolved_fields = []
        context = ResolverContext()
        for field in fields:
            # Resolve to handle relationship paths
            resolution = self._field_resolver.resolve_to_column(
                key=field, context=context
            )
            resolved_fields.append(resolution.column)

        self._state.only_fields = resolved_fields
        self._state.values_select = None  # Clear values if set
        self._state.values_list_select = None  # Clear values_list if set
        self._state.values_list_flat = False
        return self._state

    def values(self, *fields: str):
        """Return dictionaries instead of model instances (projection).

        Projects query results as dicts with specified fields. More memory-efficient
        than loading full model instances when you only need specific fields. Empty
        call returns all model fields.

        Args:
            *fields: Field names to include in result dicts. Supports relationship
                    traversal with "__". Empty means all model fields.

        Returns:
            Modified QueryState with values_select set.

        Raises:
            ModelAttributeError: If field doesn't exist.
            RelationshipError: If relationship path is invalid.

        Example:
            >>> # Specific fields
            >>> builder.values("id", "username")
            >>> # Returns: [{"id": 1, "username": "john"}, ...]

            >>> # All fields
            >>> builder.values()
            >>> # Returns: [{"id": 1, "username": "john", "email": "..."}, ...]

            >>> # Relationship fields
            >>> builder.values("title", "author__username")
            >>> # Returns: [{"title": "Post 1", "author__username": "john"}, ...]
            >>> # With annotations
            >>> builder.annotate(post_count=Count("posts"))
            >>> builder.values("username", "post_count")

        Note:
            Performance: More memory-efficient than model instances. No model
            instantiation overhead. Use for read-only data or when you need
            specific fields. Relationship traversal creates joins automatically.
        """
        if fields:
            resolved_fields = []
            context = ResolverContext()
            for field in fields:
                resolution = self._field_resolver.resolve_to_column(
                    key=field, context=context
                )
                resolved_fields.append((field, resolution.column))
        else:
            # Empty values() means every mapped column property, not an empty SELECT.
            from sqlalchemy import inspect as sa_inspect

            mapper = sa_inspect(self._state.model)
            resolved_fields = [
                (attribute.key, getattr(self._state.model, attribute.key))
                for attribute in mapper.column_attrs
            ]

        self._state.values_select = resolved_fields
        self._state.values_list_select = None
        self._state.values_list_flat = False
        self._state.only_fields = None
        return self._state

    def values_list(self, *fields: str, flat: bool = False):
        """Return tuples instead of model instances (projection).

        Projects query results as tuples. More memory-efficient than values() when
        you don't need field names. Use flat=True with single field to get flat list.

        Args:
            *fields: Field names to include in result tuples. Supports relationship
                    traversal with "__". At least one field required.
            flat: If True and single field, return flat list instead of tuples.

        Returns:
            Modified QueryState with values_list_select and flat flag set.

        Raises:
            ValueError: If flat=True with multiple fields or no fields provided.
            ModelAttributeError: If field doesn't exist.
            RelationshipError: If relationship path is invalid.

        Example:
            >>> # Multiple fields as tuples
            >>> builder.values_list("id", "username")
            >>> # Returns: [(1, "john"), (2, "jane"), ...]

            >>> # Single field as flat list
            >>> builder.values_list("username", flat=True)
            >>> # Returns: ["john", "jane", "alice", ...]

            >>> # Relationship fields
            >>> builder.values_list("title", "author__username")
            >>> # Returns: [("Post 1", "john"), ("Post 2", "jane"), ...]

        Note:
            Performance: Most memory-efficient projection method. Tuples use less
            memory than dicts. Use flat=True for single-field queries to avoid
            tuple overhead. Does not create model instances.
        """
        if type(flat) is not bool:
            raise TypeError("flat must be a bool.")
        if flat and len(fields) != 1:
            raise ValueError("flat=True requires exactly one field")

        if not fields:
            raise ValueError("values_list() requires at least one field")

        # Resolve each field to trigger joins
        resolved_fields = []
        context = ResolverContext()
        for field in fields:
            resolution = self._field_resolver.resolve_to_column(
                key=field, context=context
            )
            resolved_fields.append((field, resolution.column))

        self._state.values_select = None
        self._state.values_list_select = resolved_fields
        self._state.values_list_flat = flat
        self._state.only_fields = None
        return self._state

    def _resolve_having_condition(
        self, raw_key: str, value: Any, context: ResolverContext
    ):
        """Resolve having(key=value) pair into SQLAlchemy condition.

        Parses the annotation and lookup without promoting its relationship
        joins. HAVING is evaluated after grouping; converting a LEFT JOIN to
        INNER JOIN can incorrectly remove zero-count parent groups.

        Args:
            raw_key: Lookup key in format "annotation__lookup" or "annotation".
            value: Value to compare against.
            context: Resolution context for tracking state.

        Returns:
            SQLAlchemy condition for HAVING clause.

        Raises:
            FilterError: If annotation name doesn't exist.
        Example:
            >>> # Internal usage
            >>> condition = builder._resolve_having_condition(
            ...     "total__gte", 100, context
            ... )
            >>> # Returns: total >= 100 (using annotation's SA expression)

        Note:
            Performance: O(1) lookup in annotations dict. Annotation joins
            are already registered independently of this HAVING condition.
        """
        annotation_name, lookup = self._split_annotation_lookup(raw_key)
        # The annotation already registers all required join paths; do not
        # mark them as WHERE filters (which could promote LEFT to INNER).
        # At this point, annotation_name is guaranteed to exist in annotations
        expr = self._state.annotations[annotation_name]
        from async_orm_kit.query.expressions.analysis import contains_window

        if contains_window(expr):
            raise FilterError(
                "Window annotations cannot appear in HAVING; use a subquery"
            )
        return self._condition_builder.build(
            expr,
            lookup,
            value,
            context=context,
        )

    def _resolve_expression(self, expr, context: ResolverContext):
        """Resolve ORM expression to SQLAlchemy representation.

        Handles all expression types: aggregates, F expressions, Case/Value, and
        raw SQLAlchemy expressions. Delegates to expression's resolve() method
        when available.

        Args:
            expr: Expression to resolve (Aggregate, F, Case, Value, etc.).
            context: Resolution context for tracking state.

        Returns:
            Tuple of (sql_expr, is_aggregate) where sql_expr is SQLAlchemy expression
            and is_aggregate indicates if it's an aggregate function.

        Example:
            >>> # Internal usage
            >>> sql_expr, is_agg = builder._resolve_expression(
            ...     Count("posts"), context
            ... )
            >>> # Returns: (func.count(...), True)

            >>> sql_expr, is_agg = builder._resolve_expression(
            ...     F("price") * F("quantity"), context
            ... )
            >>> # Returns: (price * quantity, False)

        Note:
            Performance: O(1) for simple expressions. Complex expressions may trigger
            field resolution and join creation. Aggregate detection is used to set
            has_aggregates flag for GROUP BY logic.
        """
        from sqlalchemy.sql.elements import ClauseElement

        from async_orm_kit.query.expressions import Case, Value
        from async_orm_kit.query.expressions.analysis import contains_aggregate
        from async_orm_kit.query.mixins.combinable import CombinableMixin

        if isinstance(expr, Aggregate):
            sql_expr = expr.resolve(
                field_resolver=self._field_resolver, context=context
            )
            return sql_expr, True

        if isinstance(expr, (Case, Value, F, FExpression)):
            sql_expr = expr.resolve(
                field_resolver=self._field_resolver, context=context
            )
            return sql_expr, contains_aggregate(sql_expr)

        # Catch-all for any future CombinableMixin subclass
        if isinstance(expr, CombinableMixin) and hasattr(expr, "resolve"):
            sql_expr = expr.resolve(self._field_resolver, context)
            return sql_expr, contains_aggregate(sql_expr)

        if isinstance(expr, ClauseElement):
            return expr, contains_aggregate(expr)

        raise TypeError(
            "Unsupported annotation expression "
            f"{type(expr).__name__!r}; use F(), FExpression, Value(), Case(), "
            "an Aggregate, or a SQLAlchemy ClauseElement."
        )

    def _split_annotation_lookup(self, raw_key: str):
        """
        Split "annotation_name__lookup" into (annotation_name, lookup).

        Strategy:
          Walk right-to-left through tokens. The FIRST split where:
            - the right part is a known lookup operator, AND
            - the left part is a known annotation name
          wins.

        If no valid split is found, raise FilterError.

        Examples:
            "total__gte"           → ("total",        "gte")
            "count"                → ("count",        "exact")
            "revenue__sum__gt"     → ("revenue__sum", "gt")
            "total__isnull"        → ("total",        "isnull")
            "order__total__gte"    → ("order__total", "gte")
        """
        annotations = self._state.annotations

        parts = raw_key.split("__")

        # Case 3: Check if entire key is an annotation (no lookup)
        if raw_key in annotations:
            return raw_key, "exact"

        # Case 2: Walk right-to-left to find annotation_name + lookup split
        for i in range(len(parts) - 1, 0, -1):
            candidate_lookup = parts[i]
            candidate_name = "__".join(parts[:i])

            if candidate_name in annotations:
                return candidate_name, candidate_lookup

        # Case 3: Not found
        raise FilterError(
            f"having() key '{raw_key}' does not match any annotation. "
            f"Available: {list(annotations.keys())}"
        )
