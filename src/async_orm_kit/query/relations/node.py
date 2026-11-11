from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .enums import JoinType

if TYPE_CHECKING:
    from sqlalchemy.orm import RelationshipProperty


@dataclass(slots=True)
class JoinNode:
    """Represents a single JOIN operation in the query's JOIN graph.

    JoinNode is a tree node that tracks metadata about a relationship traversal
    in a Django-style query. Each node corresponds to one relationship in a path
    like "building__city__country", forming a tree structure where the root
    represents the base model and children represent joined relationships.

    The node stores usage flags that influence JOIN type optimization during
    query compilation. For example, a node marked with used_in_filter=True may
    be promoted from LEFT JOIN to INNER JOIN if it doesn't appear in OR/NOT
    contexts or nullable checks.

    This is a pure data container with minimal logic - JOIN type optimization
    and SQL compilation are handled by JoinGraph and QueryCompiler respectively.

    Attributes:
        path: Django-style relationship path from root model using double
            underscores. Examples: "", "building", "building__city__country".
            Empty string for synthetic root node.
        alias: SQLAlchemy aliased class for this joined table. Populated during
            SQL compilation by QueryCompiler, not during graph construction.
        join_type: Current JOIN type (INNER or LEFT). May be mutated by
            JoinGraph optimization passes based on usage flags.
        relationship: SQLAlchemy RelationshipProperty defining the foreign key
            relationship. None only for the synthetic root node.
        parent: Parent node in the JOIN tree. None for root node.
        children: Direct child nodes representing relationships joined from this
            node's model.
        used_in_filter: True if WHERE clause references columns through this
            JOIN. Enables LEFT → INNER promotion unless other flags prevent it.
        used_in_or_context: True if filter using this JOIN is inside OR clause.
            Prevents LEFT → INNER promotion to preserve NULL rows.
        has_nullable_check: True if filter is IS NULL or IS NOT NULL check.
            Prevents LEFT → INNER promotion as it requires NULL rows.
        used_in_negation: True if filter using this JOIN is inside NOT/exclude().
            Prevents LEFT → INNER promotion to preserve NULL rows.
        used_in_order_by: True if ORDER BY references columns through this JOIN.
            Keeps JOIN in query even if not used elsewhere.
        used_in_annotation: True if annotate() references columns through this
            JOIN. Keeps JOIN in query even if not used elsewhere.

    Example:
        >>> # Building JOIN graph for: Product.objects.filter(
        >>> #     category__parent__name="Electronics"
        >>> # )
        >>> root = JoinNode(path="", alias=Product)
        >>> category_node = JoinNode(
        ...     path="category",
        ...     relationship=Product.category.property,
        ...     used_in_filter=True,
        ...     parent=root
        ... )
        >>> parent_node = JoinNode(
        ...     path="category__parent",
        ...     relationship=Category.parent.property,
        ...     used_in_filter=True,
        ...     parent=category_node
        ... )
        >>> print(parent_node.depth)  # 2
        >>> print(parent_node.segment)  # "parent"
        >>> print(parent_node.ancestors())  # [category_node]
        >>>
        >>> # OR context prevents promotion
        >>> or_node = JoinNode(
        ...     path="supplier",
        ...     used_in_filter=True,
        ...     used_in_or_context=True
        ... )
        >>> print(or_node.is_left)  # True (stays LEFT JOIN)

    Note:
        Performance: Property access is O(1) except ancestors() which is O(d)
        where d is depth. Tree operations like add_child() are O(1).

        The node is immutable after creation except for join_type which may be
        updated by optimization passes in planning. Usage flags should be set during initial
        construction or via JoinGraph.add_node() which handles flag merging.

        Hash and equality are based solely on path, allowing nodes to be used
        in sets and dicts for deduplication during graph construction.
    """

    path: str
    alias: Any
    join_type: JoinType = JoinType.LEFT
    relationship: RelationshipProperty | None = None
    parent: JoinNode | None = None
    children: list[JoinNode] = field(default_factory=list)

    # ── Usage flags (set by the query builder) ──
    used_in_filter: bool = False
    used_in_or_context: bool = False
    has_nullable_check: bool = False
    used_in_negation: bool = False
    used_in_order_by: bool = False
    used_in_annotation: bool = False

    # ── Internal ──
    _depth: int | None = field(default=None, repr=False)

    @property
    def depth(self) -> int:
        """Calculate depth in JOIN tree with caching.

        Depth represents the number of relationship traversals from root model.
        Root node has depth 0, direct relationships have depth 1, etc. The value
        is computed once on first access and cached for subsequent calls.

        Returns:
            Integer depth where 0 is root, 1 is direct relationship, 2 is nested
            relationship, etc. Computed by counting "__" separators in path.

        Example:
            >>> root = JoinNode(path="", alias=Product)
            >>> root.depth  # 0
            >>> category = JoinNode(path="category", parent=root)
            >>> category.depth  # 1
            >>> parent = JoinNode(path="category__parent", parent=category)
            >>> parent.depth  # 2

        Note:
            Performance: O(n) on first call where n is path length, O(1) on
            subsequent calls due to caching. Cache is stored in _depth field.
        """
        if self._depth is None:
            self._depth = self.path.count("__") + 1 if self.path else 0
        return self._depth

    @property
    def is_root(self) -> bool:
        """Check if this is the synthetic root node.

        Root node represents the base model being queried and has no parent or
        path. All relationship JOINs are descendants of the root.

        Returns:
            True if this is the root node (empty path and no parent), False
            otherwise.

        Example:
            >>> root = JoinNode(path="", alias=Product)
            >>> root.is_root  # True
            >>> child = JoinNode(path="category", parent=root)
            >>> child.is_root  # False
        """
        return self.path == "" and self.parent is None

    @property
    def segment(self) -> str:
        """Extract the final relationship name from the path.

        Returns the last component of the double-underscore separated path,
        representing the relationship name on the parent model. For root node
        or single-level paths, returns the entire path.

        Returns:
            Final path segment (relationship name). Empty string for root node.

        Example:
            >>> node = JoinNode(path="building__city__country")
            >>> node.segment  # "country"
            >>> node = JoinNode(path="category")
            >>> node.segment  # "category"
            >>> root = JoinNode(path="")
            >>> root.segment  # ""
        """
        return self.path.rsplit("__", 1)[-1] if self.path else ""

    @property
    def parent_path(self) -> str:
        """Extract the parent node's path from this node's path.

        Removes the final segment from the path to get the parent's path. Used
        during graph construction to locate parent nodes when building the tree.

        Returns:
            Path of parent node. Empty string for direct children of root.

        Example:
            >>> node = JoinNode(path="building__city__country")
            >>> node.parent_path  # "building__city"
            >>> node = JoinNode(path="category")
            >>> node.parent_path  # "">>> root = JoinNode(path="")
            >>> root.parent_path  # ""
        """
        parts = self.path.rsplit("__", 1)
        return parts[0] if len(parts) > 1 else ""

    @property
    def is_inner(self) -> bool:
        """Check if this node uses INNER JOIN.

        Returns:
            True if join_type is INNER, False otherwise.

        Example:
            >>> node = JoinNode(path="category", join_type=JoinType.INNER)
            >>> node.is_inner  # True
            >>> node.is_left  # False
        """
        return self.join_type == JoinType.INNER

    @property
    def is_left(self) -> bool:
        """Check if this node uses LEFT JOIN.

        Returns:
            True if join_type is LEFT, False otherwise.

        Example:
            >>> node = JoinNode(path="category", join_type=JoinType.LEFT)
            >>> node.is_left  # True
            >>> node.is_inner  # False
        """
        return self.join_type == JoinType.LEFT

    def add_child(self, child: JoinNode) -> None:
        """Attach a direct child while preserving the graph's tree invariants."""
        if not isinstance(child, JoinNode):
            raise TypeError("child must be a JoinNode instance.")
        if child is self:
            raise ValueError("A join node cannot be its own child.")
        if child.parent is not None and child.parent is not self:
            raise ValueError(
                f"Join node {child.path!r} already belongs to another parent."
            )
        if child.parent_path != self.path:
            raise ValueError(
                f"Join node {child.path!r} is not a direct child of {self.path!r}."
            )
        if any(
            existing is child or existing.path == child.path
            for existing in self.children
        ):
            raise ValueError(f"Duplicate child path {child.path!r}.")

        current: JoinNode | None = self
        while current is not None:
            if current is child:
                raise ValueError("Attaching this node would create a cycle.")
            current = current.parent

        child.parent = self
        self.children.append(child)

    def ancestors(self) -> list[JoinNode]:
        """Return all ancestor nodes from parent to root (excluding root).

        Walks up the parent chain collecting nodes until reaching the root node.
        Useful for JOIN dependency analysis and path validation. The root node
        itself is excluded from the result.

        Returns:
            List of ancestor nodes ordered from immediate parent to most distant
            ancestor (closest to root). Empty list if this is root or direct
            child of root.

        Example:
            >>> # Tree: root -> building -> city -> country
            >>> country.ancestors()
            >>> # Returns: [city, building]>>>
            >>> building.ancestors()
            >>> # Returns: []  (direct child of root)
            >>>
            >>> root.ancestors()
            >>> # Returns: []  (is root)

        Note:
            Performance: O(d) where d is depth. Allocates a new list on each
            call - cache result if calling multiple times.
        """
        result: list[JoinNode] = []
        current = self.parent
        while current is not None and not current.is_root:
            result.append(current)
            current = current.parent
        return result

    def __hash__(self) -> int:
        """Hash based on path for use in sets and dicts.

        Allows JoinNode instances to be used as dictionary keys and set members
        for deduplication during graph construction. Only path is used for hash
        to ensure nodes with same path are considered identical.

        Returns:
            Hash value based on path string.

        Example:
            >>> node1 = JoinNode(path="category", used_in_filter=True)
            >>> node2 = JoinNode(path="category", used_in_filter=False)
            >>> hash(node1) == hash(node2)  # True
            >>> {node1, node2}  # Set with one element
        """
        return hash(self.path)

    def __eq__(self, other: object) -> bool:
        """Compare nodes by path for equality.

        Two nodes are equal if they have the same path, regardless of other
        attributes. This enables deduplication in sets and dicts during graph
        construction.

        Args:
            other: Object to compare with. Must be JoinNode instance.

        Returns:
            True if other is JoinNode with same path, False otherwise.

        Example:
            >>> node1 = JoinNode(path="category", join_type=JoinType.INNER)
            >>> node2 = JoinNode(path="category", join_type=JoinType.LEFT)
            >>> node1 == node2  # True (same path)
            >>> node3 = JoinNode(path="supplier")
            >>> node1 == node3  # False (different path)
        """
        if not isinstance(other, JoinNode):
            return NotImplemented
        return self.path == other.path

    def __repr__(self) -> str:
        """Generate debug representation with path, type, and active flags.

        Creates a compact string showing the node's path, JOIN type, and any
        active usage flags. Flags are abbreviated: F=filter, OR=or_context,
        NULL=nullable_check, NEG=negation, ORD=order_by, ANN=annotation.

        Returns:
            String representation suitable for debugging and logging.

        Example:
            >>> node = JoinNode(
            ...     path="category",
            ...     join_type=JoinType.INNER,
            ...     used_in_filter=True,
            ...     used_in_or_context=True
            ... )
            >>> repr(node)
            "JoinNode('category', inner [F,OR])"
            >>>
            >>> root = JoinNode(path="", alias=Product)
            >>> repr(root)
            "JoinNode('', left)"
        """
        flags = []
        if self.used_in_filter:
            flags.append("F")
        if self.used_in_or_context:
            flags.append("OR")
        if self.has_nullable_check:
            flags.append("NULL")
        if self.used_in_negation:
            flags.append("NEG")
        if self.used_in_order_by:
            flags.append("ORD")
        if self.used_in_annotation:
            flags.append("ANN")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        return f"JoinNode({self.path!r}, {self.join_type.value}{flag_str})"

    def _clone(self) -> JoinNode:
        """Create shallow copy of this node without parent/child relationships.

        Copies all attributes except parent and children, which are reset to
        None and empty list respectively. Used by JoinGraph.clone() to duplicate
        the graph structure while preserving node state.

        Returns:
            New JoinNode with same attributes but no tree relationships.

        Example:
            >>> original = JoinNode(
            ...     path="category",
            ...     used_in_filter=True,
            ...     parent=root
            ... )
            >>> clone = original._clone()
            >>> clone.path  # "category"
            >>> clone.used_in_filter  # True
            >>> clone.parent  # None (not copied)
            >>> clone is original  # False

        Note:
            Performance: O(1) operation. Uses __new__ to bypass dataclass
            __init__ for maximum speed. Parent/child relationships must be
            reconstructed by caller (typically JoinGraph.clone()).
        """
        node = JoinNode.__new__(JoinNode)  # skip dataclass __init__

        # Copy all fields manually (fastest)
        node.path = self.path
        node.alias = self.alias
        node.join_type = self.join_type
        node.relationship = self.relationship

        node.parent = None  # filled later
        node.children = []  # filled later

        node.used_in_filter = self.used_in_filter
        node.used_in_or_context = self.used_in_or_context
        node.has_nullable_check = self.has_nullable_check
        node.used_in_negation = self.used_in_negation
        node.used_in_order_by = self.used_in_order_by
        node.used_in_annotation = self.used_in_annotation

        node._depth = self._depth

        return node
