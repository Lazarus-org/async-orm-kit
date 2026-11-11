from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, select
from sqlalchemy.orm import joinedload, load_only, selectinload

from async_orm_kit.config import DEFAULT_CONFIG, ORMConfig
from async_orm_kit.exceptions import RelationshipError
from async_orm_kit.query.planning.directives import LoadStrategy, QueryContext
from async_orm_kit.query.planning.factory import create_default_planner
from async_orm_kit.query.relations.graph import JoinType

if TYPE_CHECKING:
    from async_orm_kit.queryset.state import QueryState


class QueryCompiler:
    """Compiles a QueryState into a SQLAlchemy SELECT statement.

    QueryCompiler is the final stage of the query pipeline. It takes the fully
    configured QueryState — including filters, joins, ordering, annotations,
    and loading preferences — and produces a single SQLAlchemy ``Select``
    statement ready for execution against a database session.

    Compilation proceeds in two phases:

    1. **Planning**: A query planner analyzes the join graph and query context
       to decide loading strategies (joinedload vs selectinload vs skip) for
       each relationship path.
    2. **Building**: The compiler assembles the SQL statement by applying each
       clause (SELECT, JOIN, WHERE, ORDER BY, etc.) in sequence, then attaches
       the planned eager-loading options.

    Responsibilities:
        - Translate QueryState into a valid SQLAlchemy Select statement.
        - Coordinate with the query planner for loading strategy decisions.
        - Apply JOINs in topological order from the JoinGraph.
        - Handle all SELECT variants (full model, values, values_list, only).
        - Apply annotations, GROUP BY, HAVING for aggregate queries.
        - Build chained loader options for nested eager loading.

    NOT Responsibilities:
        - Mutating QueryState (read-only consumer).
        - Managing database sessions or executing queries.
        - Constructing or modifying the JoinGraph.
        - Filter expression construction (already resolved in QueryState).
        - Query caching or optimization beyond loading strategy selection.

    Example:
        >>> from async_orm_kit.query.compiler import QueryCompiler
        >>> compiler = QueryCompiler()
        >>> stmt = compiler.compile(queryset_state)
        >>> # stmt is a SQLAlchemy Select ready for session.execute(stmt)
        >>>
        >>> # The compiler handles all query shapes:
        >>> # Full model:      SELECT product.* FROM product ...
        >>> # values():        SELECT product.name, product.price FROM product ...
        >>> # values_list():   SELECT product.id, product.name FROM product ...
        >>> # only():          SELECT product.(id, name) FROM product ...
        >>> # with JOINs:      ... JOIN category ON ... LEFT JOIN supplier ON ...
        >>> # with aggregates: ... GROUP BY ... HAVING ...
    """

    def __init__(self, config: ORMConfig | None = None) -> None:
        self.config = config or DEFAULT_CONFIG

    def compile(self, state: "QueryState", *, allow_outer_refs: bool = False):
        """Compile a QueryState into a SQLAlchemy SELECT statement.

        This is the main entry point for query compilation. It orchestrates the
        full compilation pipeline: planning loading strategies, building the
        SELECT clause, applying JOINs, filters, ordering, pagination, and
        eager-loading options.

        Args:
            state: Fully configured QueryState containing model, filters,
                join graph, ordering, annotations, and loading preferences.

        Returns:
            A SQLAlchemy ``Select`` statement ready for execution via
            ``session.execute()``. The statement includes all JOINs, WHERE
            conditions, ORDER BY, LIMIT/OFFSET, DISTINCT, and eager-loading
            options.

        Example:
            >>> compiler = QueryCompiler()
            >>> stmt = compiler.compile(state)
            >>> results = session.execute(stmt).scalars().all()

        Note:
            The compilation order matters. JOINs must be applied before filters
            and ordering since those clauses may reference aliased columns from
            joined tables. Loading options are applied last as they don't affect
            SQL structure.
        """
        # ── Phase 1: Plan loading strategies ──
        context = QueryContext(
            has_aggregates=state.has_aggregates,
            is_distinct=state.is_distinct or bool(state.distinct_on),
            limit=state.limit_val,
            select_related_paths=frozenset(state.select_related),
            prefetch_paths=frozenset(state.prefetch_related),
        )

        planner = create_default_planner(
            graph_mut=state.join_graph,
            max_joinedload_depth=self.config.max_joinedload_depth,
        )

        directives = planner.plan(state.join_graph, context)

        # ── Phase 2: Build SQL and apply joins ──
        stmt = self._build_select(state=state)

        stmt = self._apply_joins(stmt=stmt, state=state)
        stmt = self._apply_filters(stmt=stmt, state=state)
        stmt = self._apply_ordering(stmt=stmt, state=state)
        stmt = self._apply_limit_offset(stmt=stmt, state=state)
        stmt = self._apply_distinct(stmt=stmt, state=state)

        stmt = self._apply_loading_options(
            stmt=stmt, state=state, directives=directives
        )
        if state.for_update is not None:
            stmt = stmt.with_for_update(**state.for_update)

        if not allow_outer_refs:
            from sqlalchemy.sql import visitors

            from async_orm_kit.exceptions import QueryStateError
            from async_orm_kit.query.expressions.subquery import _OUTER_REF

            if any(
                getattr(element, "_annotations", {}).get(_OUTER_REF) is not None
                for element in visitors.iterate(stmt)
            ):
                raise QueryStateError(
                    "OuterRef can only be evaluated inside Subquery() or Exists()"
                )

        return stmt

    # ---------------------------------------------------------
    # SELECT
    # ---------------------------------------------------------

    def _build_select(self, state: "QueryState"):
        """Build the SELECT clause based on query shape.

        Determines which SELECT variant to use based on QueryState configuration,
        in priority order:

        1. ``values()`` — select specific columns by name, returns dicts.
        2. ``values_list()`` — select specific columns, returns tuples.
        3. ``only()`` — load full model but defer non-specified columns.
        4. Full model — ``SELECT model.*``.

        For each variant, annotations are appended and GROUP BY / HAVING are
        applied if the query contains aggregates.

        Args:
            state: QueryState with model, values_select, values_list_select,
                only_fields, annotations, and aggregate information.

        Returns:
            SQLAlchemy ``Select`` statement with the appropriate columns and
            GROUP BY / HAVING clauses if needed.

        Note:
            The priority order means ``values()`` takes precedence over
            ``values_list()``, which takes precedence over ``only()``. This
            matches Django's behavior where the last call wins, but here
            the QueryState should only have one set.
        """
        qs = state

        # values()
        if qs.values_select is not None:
            columns = [col.label(name) for name, col in qs.values_select]
            stmt = select(*columns)
            stmt = self._apply_annotations(stmt=stmt, state=state)

            if qs.has_aggregates:
                stmt = self._apply_group_by(
                    stmt=stmt, columns=qs.group_by_fields or columns
                )
                stmt = self._apply_having(stmt=stmt, state=state)

            return stmt

        # values_list()
        if qs.values_list_select is not None:
            columns = [col.label(name) for name, col in qs.values_list_select]
            stmt = select(*columns)
            stmt = self._apply_annotations(stmt=stmt, state=state)

            if qs.has_aggregates:
                stmt = self._apply_group_by(stmt, qs.group_by_fields or columns)
                stmt = self._apply_having(stmt=stmt, state=state)

            return stmt

        # only() - load specific fields
        if qs.only_fields is not None:
            stmt = select(qs.model).options(load_only(*qs.only_fields))
            stmt = self._apply_annotations(stmt=stmt, state=state)

            if qs.has_aggregates:
                stmt = self._apply_group_by(
                    stmt=stmt, columns=qs.group_by_fields or qs.only_fields
                )
                stmt = self._apply_having(stmt=stmt, state=state)

            return stmt

        # full model
        stmt = select(qs.model)
        if qs.deferred_fields:
            from sqlalchemy.orm import defer as sa_defer

            stmt = stmt.options(
                *(sa_defer(getattr(qs.model, field)) for field in qs.deferred_fields)
            )
        stmt = self._apply_annotations(stmt=stmt, state=state)

        if qs.has_aggregates:
            # aliased(Model) does not expose __table__; group by instrumented
            # mapped attributes of the actual entity/alias, not the base table.
            from sqlalchemy import inspect as sa_inspect

            insp: Any = sa_inspect(qs.model)
            mapper = insp.mapper if insp.is_aliased_class else insp
            group_by_cols = [
                getattr(qs.model, attr.key) for attr in mapper.column_attrs
            ]
            stmt = self._apply_group_by(
                stmt=stmt, columns=qs.group_by_fields or group_by_cols
            )
            stmt = self._apply_having(stmt=stmt, state=state)

        return stmt

    # ---------------------------------------------------------
    # JOINS
    # ---------------------------------------------------------

    def _apply_joins(self, stmt, state: "QueryState"):
        """Apply JOIN clauses to the statement from the join graph.

        Iterates over the join graph's topological order and emits either
        ``JOIN`` (INNER) or ``LEFT OUTER JOIN`` for each node. The topological
        order guarantees that parent tables are joined before children, so
        alias references are always valid.

        Each JOIN uses the parent node's alias as the left side and the current
        node's alias as the right side, with the relationship attribute as the
        ON condition.

        Args:
            stmt: SQLAlchemy ``Select`` statement to add JOINs to.
            state: QueryState containing the join graph.

        Returns:
            Statement with all JOINs applied in topological order.

        Example:
            >>> # For join graph: root -> category (LEFT) -> parent (INNER)
            >>> # Produces:
            >>> # ... LEFT JOIN category AS category_1 ON product.category_id = category_1.id
            >>> #     JOIN category AS category_2 ON category_1.parent_id = category_2.id
        """
        for node in state.join_graph.join_order:
            if not node.parent or not node.relationship:
                raise RelationshipError("Join nodes require a parent and relationship")

            parent_alias = node.parent.alias
            alias = node.alias
            rel_attr = getattr(parent_alias, node.relationship.key)

            if node.join_type == JoinType.INNER:
                stmt = stmt.join(alias, rel_attr)
            else:
                stmt = stmt.outerjoin(alias, rel_attr)

        return stmt

    # ---------------------------------------------------------
    # FILTERS
    # ---------------------------------------------------------

    def _apply_filters(self, stmt, state: "QueryState"):
        """Apply WHERE clause conditions to the statement.

        Combines all filter expressions from QueryState using AND logic.
        Filter expressions are pre-built SQLAlchemy column expressions that
        may reference aliased columns from joined tables.

        Args:
            stmt: SQLAlchemy ``Select`` statement to add WHERE clause to.
            state: QueryState containing pre-built filter expressions.

        Returns:
            Statement with WHERE clause if filters exist, unchanged otherwise.
        """
        if state.filters:
            stmt = stmt.where(and_(*state.filters))
        return stmt

    # ---------------------------------------------------------
    # ORDERING
    # ---------------------------------------------------------

    def _apply_ordering(self, stmt, state: "QueryState"):
        """Apply ORDER BY clause to the statement.

        Uses pre-built ordering expressions from QueryState, which may include
        ``asc()``, ``desc()``, and ``nulls_first()``/``nulls_last()`` modifiers.

        Args:
            stmt: SQLAlchemy ``Select`` statement to add ORDER BY to.
            state: QueryState containing ordering expressions.

        Returns:
            Statement with ORDER BY clause if ordering exists, unchanged otherwise.
        """
        if state.order_by:
            stmt = stmt.order_by(*state.order_by)
        return stmt

    # ---------------------------------------------------------
    # LIMIT / OFFSET
    # ---------------------------------------------------------

    def _apply_limit_offset(self, stmt, state: "QueryState"):
        """Apply LIMIT and OFFSET pagination to the statement.

        Args:
            stmt: SQLAlchemy ``Select`` statement to paginate.
            state: QueryState containing limit_val and offset_val. Either or
                both may be None.

        Returns:
            Statement with LIMIT and/or OFFSET applied as needed.
        """
        if state.limit_val is not None:
            stmt = stmt.limit(state.limit_val)

        if state.offset_val is not None:
            stmt = stmt.offset(state.offset_val)

        return stmt

    # ---------------------------------------------------------
    # DISTINCT
    # ---------------------------------------------------------

    def _apply_distinct(self, stmt, state: "QueryState"):
        """Apply DISTINCT or DISTINCT ON to the statement.

        Supports two modes:
        - ``DISTINCT ON (col1, col2, ...)`` — PostgreSQL-specific, deduplicates
          by specific columns.
        - ``DISTINCT`` — standard SQL, deduplicates entire rows.

        ``DISTINCT ON`` takes precedence if both are set.

        Args:
            stmt: SQLAlchemy ``Select`` statement.
            state: QueryState with ``distinct_on`` (list of columns) and/or
                ``is_distinct`` (boolean flag).

        Returns:
            Statement with DISTINCT applied as needed.
        """
        qs = state

        if qs.distinct_on:
            try:
                from sqlalchemy.dialects.postgresql import distinct_on
            except ImportError:  # SQLAlchemy 2.0 compatibility
                stmt = stmt.distinct(*qs.distinct_on)
            else:
                stmt = stmt.ext(distinct_on(*qs.distinct_on))

        elif qs.is_distinct:
            stmt = stmt.distinct()

        return stmt

    # ---------------------------------------------------------
    # ANNOTATIONS
    # ---------------------------------------------------------

    def _apply_annotations(self, stmt, state: "QueryState"):
        """Append annotation expressions as labeled columns to the statement.

        Annotations are named SQL expressions (e.g., ``Count``, ``Sum``,
        ``F`` expressions) that appear in the SELECT clause. Each annotation
        is added via ``add_columns()`` with a label matching its name.

        Args:
            stmt: SQLAlchemy ``Select`` statement.
            state: QueryState containing ``annotations`` dict mapping names to
                SQLAlchemy column expressions.

        Returns:
            Statement with annotation columns appended.

        Example:
            >>> # annotations = {"total_price": func.sum(Product.price)}
            >>> # Produces: SELECT ..., SUM(product.price) AS total_price
        """
        if state.annotations:
            # Explicit projection of an annotation already selects its SQL
            # expression. Appending it again duplicates a column and breaks
            # values_list(flat=True), set-operation arity and scalar CTEs.
            selected = (
                state.values_select
                if state.values_select is not None
                else state.values_list_select
            )
            selected_names = (
                {name for name, _ in selected} if selected is not None else set()
            )
            extra = [
                expr.label(name)
                for name, expr in state.annotations.items()
                if name not in selected_names
            ]
            if extra:
                stmt = stmt.add_columns(*extra)
        return stmt

    # ---------------------------------------------------------
    # GROUP BY
    # ---------------------------------------------------------

    def _apply_group_by(self, stmt, columns):
        """Apply GROUP BY clause to the statement.

        Used when the query contains aggregate annotations. Groups by the
        provided columns, which are typically the non-aggregate columns in
        the SELECT clause.

        Args:
            stmt: SQLAlchemy ``Select`` statement.
            columns: List of column expressions to group by. For full model
                queries, this is all table columns. For ``values()`` queries,
                only the selected columns.

        Returns:
            Statement with GROUP BY clause if columns are provided.
        """
        if columns:
            stmt = stmt.group_by(*columns)
        return stmt

    # ---------------------------------------------------------
    # HAVING
    # ---------------------------------------------------------

    def _apply_having(self, stmt, state: "QueryState"):
        """Apply HAVING clause for aggregate-level filtering.

        HAVING conditions filter groups after GROUP BY aggregation, unlike
        WHERE which filters individual rows. Combined using AND logic.

        Args:
            stmt: SQLAlchemy ``Select`` statement with GROUP BY already applied.
            state: QueryState containing ``having_conditions`` list of
                SQLAlchemy expressions.

        Returns:
            Statement with HAVING clause if conditions exist.

        Example:
            >>> # having_conditions = [func.count(Order.id) > 5]
            >>> # Produces: ... HAVING COUNT(order.id) > 5
        """
        if state.having_conditions:
            stmt = stmt.having(and_(*state.having_conditions))
        return stmt

    # ---------------------------------------------------------
    # EAGER LOADING
    # ---------------------------------------------------------

    def _apply_loading_options(self, stmt, state: "QueryState", directives):
        """Attach eager-loading options for model-instance queries only.

        A filter JOIN is not a relationship loader, so the planner must still
        attach the requested loader even when the same path exists in the
        JoinGraph. Projection queries do not return model instances and cannot
        accept ORM relationship loader options.
        """
        if state.values_select is not None or state.values_list_select is not None:
            return stmt

        options = []
        for directive in directives:
            if directive.strategy == LoadStrategy.SKIP:
                continue

            path_parts = directive.path.split("__")
            if directive.strategy == LoadStrategy.JOINEDLOAD:
                loader = self._build_joinedload_chain(
                    state.model, path_parts, directive.segment_strategies
                )
            else:
                loader = self._build_selectinload_chain(
                    state.model, path_parts, directive.segment_strategies
                )
            options.append(loader)

        return stmt.options(*options) if options else stmt

    def _build_joinedload_chain(self, model, path_parts, segment_strategies=()):
        """Build a validated joinedload chain, honoring mixed segment strategies."""
        return self._build_loader_chain(
            model, path_parts, LoadStrategy.JOINEDLOAD, segment_strategies
        )

    def _build_selectinload_chain(self, model, path_parts, segment_strategies=()):
        """Build a validated selectinload chain, honoring mixed segment strategies."""
        return self._build_loader_chain(
            model, path_parts, LoadStrategy.SELECTINLOAD, segment_strategies
        )

    @staticmethod
    def _build_loader_chain(
        model,
        path_parts,
        default_strategy: LoadStrategy,
        segment_strategies=(),
    ):
        """Resolve relationship attributes before constructing loader options."""
        from sqlalchemy import inspect as sa_inspect

        if not path_parts or any(
            not isinstance(part, str) or not part for part in path_parts
        ):
            raise RelationshipError("Eager-loading path must contain valid segments.")
        if segment_strategies and len(segment_strategies) != len(path_parts):
            raise RelationshipError(
                "Eager-loading strategy count does not match relationship path depth."
            )

        current_model = model
        loader = None
        for index, part in enumerate(path_parts):
            mapper = sa_inspect(current_model)
            relationship = mapper.relationships.get(part)
            if relationship is None:
                raise RelationshipError(
                    f"'{current_model.__name__}' has no relationship {part!r}."
                )

            attribute = getattr(current_model, relationship.key)
            strategy = (
                segment_strategies[index] if segment_strategies else default_strategy
            )
            if loader is None:
                loader = (
                    joinedload(attribute)
                    if strategy == LoadStrategy.JOINEDLOAD
                    else selectinload(attribute)
                )
            elif strategy == LoadStrategy.JOINEDLOAD:
                loader = loader.joinedload(attribute)
            else:
                loader = loader.selectinload(attribute)

            current_model = relationship.mapper.class_

        return loader
