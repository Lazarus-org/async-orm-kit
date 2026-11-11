from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy.orm import RelationshipProperty, aliased

from async_orm_kit._logging import get_logger
from async_orm_kit.exceptions import RelationshipError

from .enums import JoinType
from .node import JoinNode

logger = get_logger(__name__)


class JoinGraph:
    """Manages the directed acyclic graph of JOIN operations for a query.

    JoinGraph is the central data structure for tracking all JOIN operations
    required by a query. It maintains a tree of JoinNode instances representing
    relationship traversals, ensures topological ordering for SQL emission, and
    provides both read-only and mutable interfaces for query optimization passes.

    The graph starts with a synthetic root node representing the base model's
    table (FROM clause). All relationship JOINs are descendants of this root,
    forming a tree structure where each path like "building__city__country"
    creates a chain of parent-child nodes.

    Key Features:
    - Path-based node registration with automatic ancestor creation
    - Topological ordering guaranteeing parents appear before children
    - Usage flag tracking (filter, OR context, nullable checks, etc.)
    - JOIN type management (LEFT/INNER) with promotion support
    - Relationship resolution via SQLAlchemy introspection
    - Tree traversal utilities (BFS, DFS, subtree extraction)
    - Validation and integrity checking
    - Deep cloning for query optimization passes

    The graph satisfies two protocols:
    - JoinGraphReadable: Read-only access for planning passes
    - JoinGraphMutable: Narrow mutation interface for JOIN promotionResponsibilities:
    - Store and index JOIN nodes by path
    - Maintain topological JOIN order for SQL emission
    - Resolve SQLAlchemy relationships from Django-style paths
    - Merge usage flags when paths are registered multiple times
    - Detect and prevent cycles (though tree structure prevents this)
    - Provide traversal and query utilities

    NOT Responsibilities (Handled via Query Planner):
    - Loading strategy decisions (joinedload vs selectinload)
    - Tracking select_related/prefetch_related paths
    - Aggregate awareness or Cartesian product detection
    - Any query planning logic (handled by separate passes)

    Args:
        root_model: SQLAlchemy model class at the root of the query. Used for
            relationship resolution when traversing paths.

    Example:
        >>> # Build graph for: Product.objects.filter(
        >>> #     category__parent__name="Electronics",
        >>> #     supplier__country="US"
        >>> # )
        >>> graph = JoinGraph(Product)
        >>>
        >>> # Add first filter path
        >>> node1 = graph.add_node(
        ...     "category__parent",
        ...     used_in_filter=True
        ... )
        >>> # Automatically creates "category" node as ancestor
        >>>
        >>> # Add second filter path
        >>> node2 = graph.add_node(
        ...     "supplier__country",
        ...     used_in_filter=True
        ... )
        >>>
        >>> # Query graph state
        >>> graph.size  # 4 (category, category__parent, supplier, supplier__country)
        >>> graph.max_depth()  # 2
        >>> len(graph.filter_nodes())  # 2
        >>>
        >>> # Topological order for SQL emission
        >>> for node in graph.join_order:
        ...     print(f"{node.path} -> {node.join_type.value}")
        >>> # Output:
        >>> # category -> left
        >>> # category__parent -> left
        >>> # supplier -> left
        >>> # supplier__country -> left
        >>>
        >>> # Promote JOIN type (done by optimization pass)
        >>> graph.set_join_type("category__parent", "INNER")
        >>>
        >>> # Visualize structure
        >>> print(graph.to_ascii_tree())
        >>> # <root> (Product)
        >>> # ├── category [LEFT]
        >>> # │   └── parent [INNER] [filter]
        >>> # └── supplier [LEFT]
        >>> #     └── country [LEFT] [filter]

    Note:
        Performance: Node lookup is O(1) via path registry. Adding nodes is
        O(d) where d is depth due to ancestor creation. Traversal is O(n)
        where n is number of nodes.

        The graph is mutable during query construction but should be treated
        as read-only during SQL compilation. Use clone() to create a copy
        for optimization passes that need to experiment with changes.

        Usage flags are merged with OR semantics - once a flag is set to True
        for a path, it remains True even if the path is registered again with
        the flag as False. This ensures conservative JOIN optimization.
    """

    __slots__ = (
        "_root_model",
        "_root",
        "_path_registry",
        "_join_order",
    )

    def __init__(self, root_model: type) -> None:
        """Initialize empty JOIN graph with root model.

        Creates synthetic root node representing the base model's table and
        initializes empty registries for path tracking and topological ordering.

        Args:
            root_model: SQLAlchemy model class for the query's FROM clause.

        Example:
            >>> graph = JoinGraph(Product)
            >>> graph.root_model  # Product
            >>> graph.is_empty  # True
            >>> graph.root.is_root  # True
        """
        self._root_model = root_model
        self._root = JoinNode(path="", join_type=JoinType.INNER, alias=root_model)
        self._path_registry: OrderedDict[str, JoinNode] = OrderedDict()
        self._join_order: list[JoinNode] = []

    @property
    def root(self) -> JoinNode:
        """Return the synthetic root node representing the FROM table.

        The root node has empty path, INNER join type, and no parent. All
        relationship JOINs are descendants of this node.

        Returns:
            Root JoinNode with path="" and alias=root_model.

        Example:
            >>> graph = JoinGraph(Product)
            >>> graph.root.path  # ""
            >>> graph.root.is_root  # True
            >>> graph.root.alias  # Product
        """
        return self._root

    @property
    def root_model(self) -> type:
        """Return the SQLAlchemy model class at the root of the query.

        Returns:
            Model class used for relationship resolution.

        Example:
            >>> graph = JoinGraph(Product)
            >>> graph.root_model  # <class 'Product'>
        """
        return self._root_model

    @property
    def join_order(self) -> Sequence[JoinNode]:
        """Return topologically ordered list of JOIN nodes.

        Guarantees that for every node in the list, its parent appears earlier.
        This is the order JOINs should be emitted in SQL to satisfy dependency
        requirements.

        Returns:
            Sequence of JoinNode instances in topological order. Empty if no
            JOINs have been added.

        Example:
            >>> graph.add_node("category__parent")
            >>> graph.add_node("supplier")
            >>> [n.path for n in graph.join_order]
            >>> # ['category', 'category__parent', 'supplier']

        Note:
            Satisfies JoinGraphReadable.join_order protocol requirement.
        """
        return tuple(self._join_order)

    @property
    def size(self) -> int:
        """Return number of JOIN nodes excluding root.

        Returns:
            Count of registered JOIN paths. Zero for queries with no JOINs.

        Example:
            >>> graph = JoinGraph(Product)
            >>> graph.size  # 0
            >>> graph.add_node("category")
            >>> graph.size  # 1
            >>> graph.add_node("category__parent")
            >>> graph.size  # 2 (both category and category__parent)
        """
        return len(self._path_registry)

    @property
    def is_empty(self) -> bool:
        """Check if graph has no JOIN nodes.

        Returns:
            True if no JOINs have been added, False otherwise.

        Example:
            >>> graph = JoinGraph(Product)
            >>> graph.is_empty  # True
            >>> graph.add_node("category")
            >>> graph.is_empty  # False
        """
        return self.size == 0

    @property
    def paths(self) -> Sequence[str]:
        """Return all registered JOIN paths in insertion order.

        Returns:
            List of path strings. Order matches the order paths were first
            added to the graph.

        Example:
            >>> graph.add_node("supplier")
            >>> graph.add_node("category__parent")
            >>> graph.paths
            >>> # ['supplier', 'category', 'category__parent']
            >>> # Note: 'category' auto-created as ancestor
        """
        return tuple(self._path_registry.keys())

    def has_node(self, path: str) -> bool:
        """Check if a path is registered in the graph.

        Args:
            path: Django-style relationship path to check.

        Returns:
            True if path exists in graph, False otherwise.

        Example:
            >>> graph.add_node("category")
            >>> graph.has_node("category")  # True
            >>> graph.has_node("supplier")  # False
            >>> "category" in graph  # True (via __contains__)

        Note:
            Satisfies JoinGraphReadable.has_node protocol requirement.
            Performance: O(1) dictionary lookup.
        """
        return path in self._path_registry

    def get_node(self, path: str) -> JoinNode | None:
        """Retrieve a JOIN node by its path.

        Args:
            path: Django-style relationship path to retrieve.

        Returns:
            JoinNode if path exists, None otherwise.

        Example:
            >>> node = graph.add_node("category")
            >>> retrieved = graph.get_node("category")
            >>> retrieved is node  # True
            >>> graph.get_node("nonexistent")  # None

        Note:
            Satisfies JoinGraphReadable.get_node protocol requirement.
            Performance: O(1) dictionary lookup.
        """
        return self._path_registry.get(path)

    def add_node(
        self,
        path: str,
        relationship: RelationshipProperty | None = None,
        join_type: JoinType = JoinType.LEFT,
        *,
        used_in_filter: bool = False,
        used_in_or_context: bool = False,
        has_nullable_check: bool = False,
        used_in_negation: bool = False,
        used_in_order_by: bool = False,
        used_in_annotation: bool = False,
    ) -> JoinNode:
        """Add or update a JOIN node in the graph with usage tracking.

        If the path already exists, merges usage flags using OR semantics (flags
        can only be widened, never narrowed). If intermediate path segments don't
        exist, creates them automatically. For example, adding "a__b__c" ensures
        "a" and "a__b" exist first.

        The relationship parameter is optional - if not provided, the method
        resolves it by walking the model hierarchy using SQLAlchemy introspection.

        Args:
            path: Double-underscore delimited relationship path.
                Examples: "category", "building__city__country".
            relationship: SQLAlchemy RelationshipProperty for the final segment.
                If None, resolved automatically from models.
            join_type: Initial JOIN type (LEFT or INNER). May be promoted later
                by optimization passes.
            used_in_filter: True if WHERE clause references columns through this
                JOIN. Enables LEFT → INNER promotion.
            used_in_or_context: True if filter using this JOIN is inside OR
                clause. Prevents LEFT → INNER promotion.
            has_nullable_check: True if filter is IS NULL or IS NOT NULL check.
                Prevents LEFT → INNER promotion.
            used_in_negation: True if filter using this JOIN is inside NOT or
                exclude(). Prevents LEFT → INNER promotion.
            used_in_order_by: True if ORDER BY references columns through this
                JOIN. Keeps JOIN in query.
            used_in_annotation: True if annotate() references columns through
                this JOIN. Keeps JOIN in query.

        Returns:
            The newly created or existing JoinNode. If existing, usage flags
            are merged and join_type may be promoted to INNER.

        Raises:
            ValueError: If path creates a cycle (should never happen with tree
                structure, but guarded anyway).

        Example:
            >>> # Add simple JOIN
            >>> node = graph.add_node("category", used_in_filter=True)
            >>> node.path  # "category"
            >>> node.used_in_filter  # True
            >>>
            >>> # Add nested JOIN (auto-creates ancestors)
            >>> node = graph.add_node(
            ...     "category__parent__grandparent",
            ...     used_in_filter=True
            ... )
            >>> graph.size  # 3 (category, parent, grandparent)
            >>>
            >>> # Re-add existing path (merges flags)
            >>> node = graph.add_node(
            ...     "category",
            ...     used_in_order_by=True
            ... )
            >>> node.used_in_filter  # True (preserved)
            >>> node.used_in_order_by  # True (added)
            >>>
            >>> # OR context prevents promotion
            >>> node = graph.add_node(
            ...     "supplier",
            ...     used_in_filter=True,
            ...     used_in_or_context=True
            ... )
            >>> node.is_left  # True (stays LEFT despite filter)

        Note:
            Performance: O(d) where d is depth, due to ancestor creation and
            relationship resolution. Each ancestor requires model introspection.

            Usage flags use OR semantics - once True, they stay True. This
            ensures conservative JOIN optimization that doesn't break queries.

            The method is idempotent - calling multiple times with the same
            path returns the same node (by path equality) with merged flags.
        """
        path = self._validate_path(path)
        try:
            join_type = JoinType(join_type)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid join type {join_type!r}. "
                f"Expected one of: {[item.value for item in JoinType]}."
            ) from exc

        relationships = self.require_relationship_path(path)
        resolved_relationship = relationships[-1]
        if relationship is not None and (
            relationship.key != resolved_relationship.key
            or relationship.parent.class_ is not resolved_relationship.parent.class_
        ):
            raise ValueError(
                f"Relationship {relationship!r} does not match path {path!r}."
            )
        relationship = resolved_relationship

        # ── Return existing node if already registered ──
        existing = self._path_registry.get(path)
        if existing is not None:
            # Merge usage flags (widen, never narrow)
            existing.used_in_filter = existing.used_in_filter or used_in_filter
            existing.used_in_or_context = (
                existing.used_in_or_context or used_in_or_context
            )
            existing.has_nullable_check = (
                existing.has_nullable_check or has_nullable_check
            )
            existing.used_in_negation = existing.used_in_negation or used_in_negation
            existing.used_in_order_by = existing.used_in_order_by or used_in_order_by
            existing.used_in_annotation = (
                existing.used_in_annotation or used_in_annotation
            )

            # Promote join type: if anyone needs INNER, use INNER
            if join_type == JoinType.INNER:
                existing.join_type = JoinType.INNER

            logger.debug(
                "join_node_merged",
                path=path,
                flags=self._flag_summary(existing),
            )
            return existing

        # ── Ensure all ancestors exist ──
        self._ensure_ancestors(
            path=path,
            used_in_filter=used_in_filter,
            used_in_or_context=used_in_or_context,
            has_nullable_check=has_nullable_check,
            used_in_negation=used_in_negation,
            used_in_order_by=used_in_order_by,
            used_in_annotation=used_in_annotation,
        )

        # ── Resolve parent ──
        parent_path = self._parent_path_of(path)
        parent = self._path_registry[parent_path] if parent_path else self._root

        model = relationship.mapper.class_
        alias = aliased(model)

        # ── Create node ──
        node = JoinNode(
            path=path,
            alias=alias,
            join_type=join_type,
            relationship=relationship,
            used_in_filter=used_in_filter,
            used_in_or_context=used_in_or_context,
            has_nullable_check=has_nullable_check,
            used_in_negation=used_in_negation,
            used_in_order_by=used_in_order_by,
            used_in_annotation=used_in_annotation,
        )

        # ── Link into tree ──
        parent.add_child(node)

        # ── Register ──
        self._path_registry[path] = node
        self._join_order.append(node)

        logger.debug(
            "join_node_added",
            path=path,
            join_type=join_type.value,
            parent=parent.path or "<root>",
        )

        return node

    def remove_node(self, path: str) -> bool:
        """
        Remove a leaf node from the graph.

        Only leaf nodes (no children) can be removed. Attempting
        to remove a non-leaf raises ValueError.

        Returns True if removed, False if path not found.
        """
        node = self._path_registry.get(path)
        if node is None:
            return False

        if node.children:
            raise ValueError(
                f"Cannot remove non-leaf node '{path}' — "
                f"has {len(node.children)} children. "
                f"Remove children first."
            )

        # Unlink from parent
        if node.parent is not None:
            node.parent.children = [c for c in node.parent.children if c is not node]

        # Remove from registry and order
        del self._path_registry[path]
        self._join_order = [n for n in self._join_order if n is not node]

        logger.debug("join_node_removed", path=path)
        return True

    # ──────────────────────────────────────────────
    #  JoinGraphMutable interface
    # ──────────────────────────────────────────────

    def set_join_type(self, path: str, join_type: str) -> None:
        """
        Change the join type of an existing node.

        This is the narrow mutable interface exposed to
        planning passes (specifically JoinPromotionPass).

        Satisfies JoinGraphMutable.set_join_type

        Parameters
        ----------
        path : str
            Must be an existing path.
        join_type : str
            'INNER' or 'LEFT'.

        Raises
        ------
        KeyError
            If path does not exist.
        ValueError
            If join_type is not recognized.
        """
        node = self._path_registry.get(path)
        if node is None:
            raise KeyError(f"No join node at path '{path}'")

        try:
            new_type = JoinType(join_type)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid join type {join_type!r}. "
                f"Expected one of: {[jt.value for jt in JoinType]}"
            ) from exc

        old_type = node.join_type
        node.join_type = new_type

        logger.debug(
            "join_type_changed",
            path=path,
            old=old_type.value,
            new=new_type.value,
        )

    # ──────────────────────────────────────────────
    #  Relationship resolution
    # ──────────────────────────────────────────────

    def resolve_relationship_path(
        self, path: str
    ) -> tuple[RelationshipProperty, ...] | None:
        """Resolve every relationship in a ``__``-separated path.

        Returns ``None`` when the path is malformed or a segment is not a
        relationship. Mapper configuration errors are allowed to propagate so
        they are not silently misreported as a missing relationship.
        """
        if not isinstance(path, str) or not path:
            return None

        segments = path.split("__")
        if any(not segment or segment.strip() != segment for segment in segments):
            return None

        from sqlalchemy import inspect as sa_inspect

        current_model = self._root_model
        relationships: list[RelationshipProperty] = []

        for segment in segments:
            inspected: Any = sa_inspect(current_model)
            mapper = inspected.mapper if inspected.is_aliased_class else inspected
            relationship = mapper.relationships.get(segment)
            if relationship is None:
                return None
            relationships.append(relationship)
            current_model = relationship.mapper.class_

        return tuple(relationships)

    def require_relationship_path(self, path: str) -> tuple[RelationshipProperty, ...]:
        """Resolve a complete relationship path or raise ``RelationshipError``."""
        relationships = self.resolve_relationship_path(path)
        if relationships is None:
            raise RelationshipError(
                f"Invalid relationship path {path!r} on model "
                f"{self._root_model.__name__}."
            )
        return relationships

    def resolve_relationship(self, path: str) -> RelationshipProperty | None:
        """Resolve the final relationship in a complete path, if it exists."""
        relationships = self.resolve_relationship_path(path)
        return relationships[-1] if relationships else None

    def _resolve_relationship_from_model(
        self, path: str
    ) -> RelationshipProperty | None:
        """Backward-compatible internal helper for final-segment resolution."""
        return self.resolve_relationship(path)

    @staticmethod
    def _validate_path(path: str) -> str:
        """Validate the internal relationship-path representation."""
        if not isinstance(path, str):
            raise TypeError("Relationship path must be a string.")
        if not path or path.startswith("__") or path.endswith("__"):
            raise ValueError("Relationship path must contain non-empty segments.")
        segments = path.split("__")
        if any(not segment for segment in segments):
            raise ValueError("Relationship path must contain non-empty segments.")
        if any(segment.strip() != segment for segment in segments):
            raise ValueError("Relationship path contains an invalid segment.")
        return path

    def walk_bfs(self) -> Iterator[JoinNode]:
        """
        Breadth-first traversal starting from root's children.
        Yields nodes in the order suitable for SQL JOIN emission.
        """
        from collections import deque

        queue = deque(self._root.children)
        visited = {id(self._root)}
        while queue:
            node = queue.popleft()
            identity = id(node)
            if identity in visited:
                continue
            visited.add(identity)
            yield node
            queue.extend(node.children)

    def walk_dfs(self) -> Iterator[JoinNode]:
        """Depth-first traversal from root's children."""
        stack = list(reversed(self._root.children))
        visited = {id(self._root)}
        while stack:
            node = stack.pop()
            identity = id(node)
            if identity in visited:
                continue
            visited.add(identity)
            yield node
            stack.extend(reversed(node.children))

    def subtree(self, path: str) -> list[JoinNode]:
        """
        Return all nodes in the subtree rooted at `path`,
        including the node itself.
        """
        root_node = self._path_registry.get(path)
        if root_node is None:
            return []

        result: list[JoinNode] = []
        stack = [root_node]
        visited: set[int] = {id(self._root)}
        while stack:
            node = stack.pop()
            identity = id(node)
            if identity in visited:
                continue
            visited.add(identity)
            result.append(node)
            stack.extend(reversed(node.children))
        return result

    def leaf_nodes(self) -> list[JoinNode]:
        """Return all leaf nodes (no children)."""
        return [n for n in self._join_order if not n.children]

    # ──────────────────────────────────────────────
    #  Query information
    # ──────────────────────────────────────────────

    def filter_nodes(self) -> list[JoinNode]:
        """Nodes that are used in WHERE clauses."""
        return [n for n in self._join_order if n.used_in_filter]

    def inner_joins(self) -> list[JoinNode]:
        """Nodes with INNER join type."""
        return [n for n in self._join_order if n.is_inner]

    def left_joins(self) -> list[JoinNode]:
        """Nodes with LEFT join type."""
        return [n for n in self._join_order if n.is_left]

    def max_depth(self) -> int:
        """Maximum depth of any join in the graph."""
        if not self._join_order:
            return 0
        return max(n.depth for n in self._join_order)

    # ──────────────────────────────────────────────
    #  Validation
    # ──────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Return all structural errors found in the join graph."""
        errors: list[str] = []

        registry_paths = list(self._path_registry.keys())
        order_paths = [node.path for node in self._join_order]
        if len(order_paths) != len(set(order_paths)):
            errors.append("Join order contains duplicate paths.")
        if set(order_paths) != set(registry_paths):
            errors.append("Join order and path registry contain different nodes.")

        for path, node in self._path_registry.items():
            if path != node.path:
                errors.append(
                    f"Registry key {path!r} does not match node path {node.path!r}."
                )
            if node.parent is None:
                errors.append(f"Node {path!r} has no parent reference.")
                continue
            if node.parent.path != node.parent_path:
                errors.append(
                    f"Node {path!r} is attached to {node.parent.path!r}, "
                    f"expected {node.parent_path!r}."
                )
            if not any(child is node for child in node.parent.children):
                errors.append(f"Node {path!r} is missing from its parent's children.")
            if node.relationship is None:
                errors.append(f"Node {path!r} has no relationship metadata.")

        reachable: set[str] = set()
        stack = list(reversed(self._root.children))
        while stack:
            node = stack.pop()
            if node.path in reachable:
                errors.append(
                    f"Cycle or duplicate reachability detected at {node.path!r}."
                )
                continue
            reachable.add(node.path)
            for child in reversed(node.children):
                if child.parent is not node:
                    errors.append(
                        f"Child {child.path!r} has an inconsistent parent reference."
                    )
                stack.append(child)

        unreachable = set(registry_paths) - reachable
        if unreachable:
            errors.append(f"Unreachable nodes: {sorted(unreachable)!r}.")

        seen: set[str] = {""}
        for node in self._join_order:
            if node.parent_path not in seen:
                errors.append(
                    f"Node {node.path!r} appears before its parent "
                    f"{node.parent_path!r} in join order."
                )
            seen.add(node.path)

        return errors

    def clone(self) -> JoinGraph:
        """
        deep clone of the join graph.
        Reuses alias & relationship objects. Preserves structure.
        """
        new_graph = JoinGraph(self.root_model)

        node_map: dict[str, JoinNode] = {}
        new_registry = OrderedDict()

        for old_node in self.join_order:
            new_node = old_node._clone()
            path = new_node.path

            # resolve parent
            parent_path = self._parent_path_of(path)
            parent = node_map.get(parent_path, new_graph._root)

            new_node.parent = parent
            parent.children.append(new_node)

            node_map[path] = new_node
            new_registry[path] = new_node

        new_graph._path_registry = new_registry
        new_graph._join_order = [node_map[n.path] for n in self.join_order]

        return new_graph

    def clear(self) -> None:
        """
        Reset the graph to empty state, keeping only the root node.
        Removes all joins and clears all registries. The root model
        remains unchanged.
        """
        self._root = JoinNode(path="", alias=self._root_model, join_type=JoinType.INNER)
        self._path_registry.clear()
        self._join_order.clear()

    # ──────────────────────────────────────────────
    #  Internal helpers
    # ──────────────────────────────────────────────

    def _ensure_ancestors(self, path: str, **kwargs) -> None:
        """
        Ensure all ancestor paths exist in the graph.

        For 'a__b__c', ensures 'a' and 'a__b' exist.
        Creates them with LEFT JOIN and no usage flags
        if missing.
        """
        segments = path.split("__")
        for i in range(1, len(segments)):
            ancestor_path = "__".join(segments[:i])
            if ancestor_path not in self._path_registry:
                # Recursion via add_node handles deeper ancestors
                self.add_node(path=ancestor_path, **kwargs)

    @staticmethod
    def _parent_path_of(path: str) -> str:
        """'a__b__c' → 'a__b', 'a' → ''"""
        parts = path.rsplit("__", 1)
        return parts[0] if len(parts) > 1 else ""

    @staticmethod
    def _flag_summary(node: JoinNode) -> str:
        flags = []
        if node.used_in_filter:
            flags.append("filter")
        if node.used_in_or_context:
            flags.append("or")
        if node.has_nullable_check:
            flags.append("null_check")
        if node.used_in_negation:
            flags.append("negation")
        if node.used_in_order_by:
            flags.append("order_by")
        if node.used_in_annotation:
            flags.append("annotation")
        return ", ".join(flags) or "none"

    # ──────────────────────────────────────────────
    #  Display / debugging
    # ──────────────────────────────────────────────

    def to_ascii_tree(self) -> str:
        """
        Render the join graph as an ASCII tree.

        Example output:

        <root> (RoomModel)
        ├── building [LEFT] [F]
        │   └── city [LEFT]
        │       └── country [INNER] [F]
        └── author [LEFT]
            └── publisher [LEFT]
        """
        lines: list[str] = []
        lines.append(f"<root> ({self._root_model.__name__})")
        self._render_children(self._root, "", lines)
        return "\n".join(lines)

    def _render_children(
        self,
        parent: JoinNode,
        prefix: str,
        lines: list[str],
    ) -> None:
        children = parent.children
        for i, child in enumerate(children):
            is_last = i == len(children) - 1
            connector = "└── " if is_last else "├── "
            extension = "    " if is_last else "│   "

            flags = self._flag_summary(child)
            flag_display = f" [{flags}]" if flags != "none" else ""

            lines.append(
                f"{prefix}{connector}{child.segment} "
                f"[{child.join_type.value}]"
                f"{flag_display}"
            )
            self._render_children(child, prefix + extension, lines)

    def __repr__(self) -> str:
        return f"JoinGraph(model={self._root_model.__name__}, joins={self.size})"

    def __len__(self) -> int:
        return self.size

    def __contains__(self, path: str) -> bool:
        return self.has_node(path)

    def __iter__(self) -> Iterator[JoinNode]:
        return iter(self._join_order)
