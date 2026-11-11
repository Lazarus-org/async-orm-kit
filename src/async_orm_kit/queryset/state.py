import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class QueryState:
    """Immutable snapshot of QuerySet configuration state.

    Encapsulates all query parameters and transformations applied to a QuerySet.
    Designed for efficient cloning to support QuerySet's immutable chain pattern.
    Each QuerySet operation clones the state, modifies the clone, and returns a
    new QuerySet with the updated state.

    This separation of state from behavior enables:
    - QuerySet immutability (operations return new instances)
    - Efficient query building without side effects
    - Easy state inspection and debugging
    - Clean separation between query construction and execution

    Attributes:
        model: The SQLAlchemy model class this query operates on.
        filters: List of filter conditions (WHERE clauses).
        order_by: List of ordering expressions (ORDER BY clauses).
        limit_val: Maximum number of rows to return (LIMIT).
        offset_val: Number of rows to skip (OFFSET).
        is_distinct: Whether to apply DISTINCT to remove duplicates.
        distinct_on: Fields for DISTINCT ON (PostgreSQL only).
        group_by_fields: Fields for GROUP BY aggregation.
        having_conditions: Conditions for HAVING clause (post-aggregation filters).
        annotations: Computed fields added via annotate() (e.g., aggregates, expressions).
        annotation_paths: Relationship paths for each annotation field.
        has_aggregates: Whether query contains aggregate functions (Count, Sum, etc.).
        select_related: Relationships to JOIN and load eagerly (1-to-1, many-to-1).
        prefetch_related: Relationships to load in separate queries (1-to-many, many-to-many).
        values_select: Fields to project as dicts (values() mode).
        values_list_select: Fields to project as tuples (values_list() mode).
        values_list_flat: Whether to flatten single-field values_list() results.
        only_fields: Fields to load immediately (deferred loading mode).
        join_graph: Internal join planning structure for relationship traversal.

    Example:
        >>> # QueryState is internal - users interact via QuerySet
        >>> from your_app.models import DBUser
        >>> qs = DBUser.objects.filter(active=True).order_by("-created_at")
        >>>
        >>> # Inspect internal state (debugging)
        >>> state = qs._state
        >>> print(state.model)  # <class 'User'>
        >>> print(state.filters)  # [BinaryExpression(...)]
        >>> print(state.order_by)  # [UnaryExpression(...)]
        >>>
        >>> # State is cloned on each operation
        >>> qs2 = qs.limit(10)
        >>> assert qs._state is not qs2._state  # Different objects
        >>> assert qs._state.limit_val is None  # Original unchanged
        >>> assert qs2._state.limit_val == 10  # Clone modified
    Note:
        Performance: Uses __slots__ for memory efficiency. Cloning is O(n) where
        n is the number of filters/annotations, but typically very fast since
        most lists are small. Deep copying is avoided - lists are shallow copied
        and their contents (SQLAlchemy expressions) are reused.
    """

    model: type
    filters: list[Any] = field(default_factory=list)
    order_by: list[Any] = field(default_factory=list)
    limit_val: int | None = None
    offset_val: int | None = None
    is_distinct: bool = False
    distinct_on: list[Any] = field(default_factory=list)
    group_by_fields: list[Any] = field(default_factory=list)
    having_conditions: list[Any] = field(default_factory=list)
    annotations: dict[str, Any] = field(default_factory=dict)
    annotation_paths: dict[str, list[str]] = field(default_factory=dict)
    has_aggregates: bool = False
    for_update: dict[str, Any] | None = None
    select_related: list[str] = field(default_factory=list)
    prefetch_related: list[str] = field(default_factory=list)
    values_select: list[tuple[str, Any]] | None = None
    values_list_select: list[tuple[str, Any]] | None = None
    values_list_flat: bool = False
    only_fields: list[Any] | None = None
    deferred_fields: list[str] = field(default_factory=list)
    custom_lookups: dict[str, Any] = field(default_factory=dict)
    ordering_fields: list[str] = field(default_factory=list)
    seek_active: bool = False
    join_graph: Any = field(init=False)

    def __post_init__(self) -> None:
        """Create the empty join graph required by every compiler execution path."""
        from async_orm_kit.query.relations.graph import JoinGraph

        self.join_graph = JoinGraph(self.model)

    def clone(self) -> "QueryState":
        """Create a shallow copy of this state for immutable query chaining.

        Clones the QueryState to enable QuerySet's immutable pattern where each
        operation returns a new QuerySet without modifying the original. Uses
        shallow copying for efficiency - list contents (SQLAlchemy expressions)
        are reused, not deep copied.

        Returns:
            New QueryState instance with copied lists and same scalar values.

        Example:
            >>> state = QueryState(model=User)
            >>> state.filters.append(some_condition)
            >>>
            >>> cloned = state.clone()
            >>> cloned.filters.append(another_condition)
            >>>
            >>> len(state.filters)  # 1 - original unchanged
            >>> len(cloned.filters)  # 2 - clone modified
            >>>
            >>> # Scalar values are copied
            >>> cloned.limit_val = 10
            >>> assert state.limit_val is None

        Note:
            Performance: O(n) where n is total items across all lists. Typically
            very fast since most queries have few filters/annotations. Uses
            shallow copy - SQLAlchemy expression objects are reused, not cloned.
            JoinGraph is deep cloned if present to maintain isolation.
        """
        new_state = copy.copy(self)
        new_state.filters = self.filters.copy()
        new_state.order_by = self.order_by.copy()
        new_state.is_distinct = self.is_distinct
        new_state.distinct_on = self.distinct_on.copy()
        new_state.annotations = self.annotations.copy()
        new_state.annotation_paths = {
            name: paths.copy() for name, paths in self.annotation_paths.items()
        }
        new_state.has_aggregates = self.has_aggregates
        new_state.for_update = (
            self.for_update.copy() if self.for_update is not None else None
        )
        new_state.select_related = self.select_related.copy()
        new_state.prefetch_related = self.prefetch_related.copy()
        new_state.group_by_fields = self.group_by_fields.copy()
        new_state.having_conditions = self.having_conditions.copy()
        new_state.only_fields = (
            self.only_fields.copy() if self.only_fields is not None else None
        )
        new_state.values_select = (
            self.values_select.copy() if self.values_select is not None else None
        )
        new_state.values_list_select = (
            self.values_list_select.copy()
            if self.values_list_select is not None
            else None
        )
        new_state.values_list_flat = self.values_list_flat
        new_state.deferred_fields = self.deferred_fields.copy()
        new_state.custom_lookups = self.custom_lookups.copy()
        new_state.ordering_fields = self.ordering_fields.copy()
        new_state.seek_active = self.seek_active

        # Clone join graph and planner
        new_state.join_graph = self.join_graph.clone()

        return new_state
