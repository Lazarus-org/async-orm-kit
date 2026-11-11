from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from async_orm_kit.query.relations.node import JoinNode


@dataclass(slots=True)
class FieldResolution:
    """Result of field path resolution containing column and JOIN metadata.

    FieldResolution encapsulates the complete result of resolving a Django-style
    field path to a SQLAlchemy column. It includes the resolved column, the lookup
    operator to apply, and optional JOIN metadata if relationship traversal occurred.

    This data class serves as the contract between FieldResolver and ConditionBuilder,
    providing all information needed to build a filter condition without exposing
    internal resolution details.

    Attributes:
        field_path: Full relationship path traversed, empty string for root model
                   fields. Examples: "", "building", "user__books".
        field_name: Terminal field name resolved, empty string for direct relation
                   filters. Examples: "name", "title", "".
        lookup: Lookup operator to apply. Examples: "exact", "gte", "icontains".
        column: SQLAlchemy column or alias to use in condition. Can be a Column,
               InstrumentedAttribute, or AliasedClass for direct relation filters.
        join_node: Optional JoinNode reference if relationship traversal occurred.
                  None for root model fields. Used for JOIN graph optimization.

    Example:
        >>> # Simple field resolution (no JOINs)
        >>> FieldResolution(
        ...     field_path="",
        ...     field_name="status",
        ...     lookup="exact",
        ...     column=Product.status,
        ...     join_node=None
        ... )
        >>>
        >>> # Relationship field resolution (with JOIN)
        >>> FieldResolution(
        ...     field_path="category",
        ...     field_name="name",
        ...     lookup="icontains",
        ...     column=category_alias.name,
        ...     join_node=JoinNode(path="category", ...)
        ... )
        >>>
        >>> # Direct relation filter (no terminal field)
        >>> FieldResolution(
        ...     field_path="building",
        ...     field_name="",
        ...     lookup="exact",
        ...     column=building_alias,
        ...     join_node=JoinNode(path="building", ...)
        ... )

    Note:
        The join_node reference enables ConditionBuilder to update JOIN metadata
        based on the lookup operator (e.g., marking has_nullable_check for isnull
        lookups). This bidirectional communication ensures optimal JOIN type
        selection.
    """

    field_path: str  # "building" or "books__author"
    field_name: str  # "name" or "title" or "" for direct relation
    lookup: str  # "exact", "gte", "icontains", etc.
    column: Any  # SQLAlchemy column or alias
    join_node: JoinNode | None = None  # Optional JoinNode reference


@dataclass(slots=True)
class ParsedFieldKey:
    """Result of field key parsing containing relationship and field components.

    ParsedFieldKey represents the structural decomposition of a Django-style field
    key into its constituent parts: relationship traversal path, terminal field name,
    and lookup operator. This is a pure data structure with no behavior, serving as
    the intermediate result between key parsing and column resolution.

    The parsing process uses model introspection to distinguish relationships from
    fields and lookups, but doesn't create JOINs or resolve columns - that's
    FieldResolver's job.

    Attributes:
        relationship_parts: List of relationship names to traverse in order.
                           Empty list for root model fields.           Examples: [], ["building"], ["user", "books"].
        field_name: Terminal field name to resolve on the final model.
                   Empty string for direct relation filters.
                   Examples: "name", "title", "".
        lookup: Lookup operator extracted from the key.
               Defaults to "exact" if not specified.
               Examples: "exact", "gte", "icontains".
        is_simple: True if no relationship traversal required (optimization flag).
                  False if relationship_parts is non-empty.

    Example:
        >>> # Simple field: "status"
        >>> ParsedFieldKey(
        ...     relationship_parts=[],
        ...     field_name="status",
        ...     lookup="exact",
        ...     is_simple=True
        ... )
        >>>
        >>> # Field with lookup: "price__gte"
        >>> ParsedFieldKey(
        ...     relationship_parts=[],
        ...     field_name="price",
        ...     lookup="gte",
        ...     is_simple=True
        ... )
        >>>
        >>> # Single relationship: "building__name"
        >>> ParsedFieldKey(
        ...     relationship_parts=["building"],
        ...     field_name="name",
        ...     lookup="exact",
        ...     is_simple=False
        ... )
        >>>
        >>> # Nested relationships: "user__books__title__icontains"
        >>> ParsedFieldKey(
        ...     relationship_parts=["user", "books"],
        ...     field_name="title",
        ...     lookup="icontains",
        ...     is_simple=False
        ... )
        >>>
        >>> # Direct relation filter: "building"
        >>> ParsedFieldKey(
        ...     relationship_parts=["building"],
        ...     field_name="",
        ...     lookup="exact",
        ...     is_simple=False
        ... )

    Note:
        The is_simple flag enables fast-path optimization in FieldResolver - simple
        fields can skip JOIN graph interaction entirely. This is a significant
        performance win for queries without relationship traversal.
    """

    relationship_parts: list[str]  # ["building"] or ["user", "books"]
    field_name: str  # "name" or "title" or ""
    lookup: str  # "icontains", "exact", etc.
    is_simple: bool  # True if no relationship traversal
