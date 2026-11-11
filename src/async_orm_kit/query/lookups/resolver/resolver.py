from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import QueryableAttribute, RelationshipProperty

from async_orm_kit.exceptions import ModelAttributeError
from async_orm_kit.query.lookups.resolver.context import ResolverContext
from async_orm_kit.query.lookups.resolver.types import FieldResolution, ParsedFieldKey

if TYPE_CHECKING:
    from async_orm_kit.query.relations.node import JoinNode
    from async_orm_kit.queryset.state import QueryState


class FieldResolver:
    """Resolves field paths to SQLAlchemy columns with JOIN registration.

    FieldResolver is the core component responsible for transforming Django-style
    field lookups (e.g., "category__name__icontains") into SQLAlchemy columns with
    proper JOIN registration. It bridges the gap between Django's intuitive field
    syntax and SQLAlchemy's explicit JOIN model.

    The resolver handles three key responsibilities:
    1. Parse field keys into relationship paths, field names, and lookup operators
    2. Traverse relationships and register JOINs with the query's JOIN graph
    3. Resolve terminal fields to SQLAlchemy columns on the appropriate model/alias

    Resolution follows a two-phase process:

    Phase 1: Parsing (parse_field_key)
    - Split key by "__" delimiter
    - Use model introspection to distinguish relationships from fields
    - Identify lookup operator (defaults to "exact")
    - Return ParsedFieldKey with structural information

    Phase 2: Resolution (resolve_to_column)
    - For simple fields: resolve directly on root model
    - For relationship fields: register JOINs via JOIN graph, resolve on alias
    - Handle special cases: pk alias, annotations, direct relation filters
    - Return FieldResolution with column and JOIN metadata

    The resolver maintains no state between calls except for recently resolved
    annotation paths (used for JOIN graph optimization). Each resolution is
    independent and thread-safe.

    Attributes:
        query_state: QueryState instance providing access to model, JOIN graph,
                    and annotations. This is the resolver's window into the query.
        model: Root model class for the query (cached from query_state).
        join_graph: JoinGraph instance for registering relationship JOINs.
        annotations: Dict of annotation names to SQLAlchemy expressions.
        PK_FIELD_ALIAS: Class constant "pk" for primary key field alias.

    Args:
        state: QueryState instance containing model, JOIN graph, and annotations.

    Note:
        FieldResolver is stateless except for annotation path tracking. It can be
        reused across multiple resolve_to_column() calls safely. The resolver
        doesn't cache resolutions - each call performs full parsing and resolution.

        The resolver delegates JOIN creation to JoinGraph, ensuring consistent JOIN
        behavior across the query. It never creates JOINs directly, only requests
        them through the graph's add_node() method.

        Performance is O(n) for n-level relationship traversal, with O(1) model
        introspection per level. Simple field resolution is O(1).
    """

    PK_FIELD_ALIAS = "pk"

    def __init__(
        self,
        state: QueryState,
    ):
        """Initialize FieldResolver with query state.

        Args:
            state: QueryState instance providing access to model, JOIN graph,and annotations for the current query.
        """
        self.query_state = state
        self.model = self.query_state.model
        self.join_graph = self.query_state.join_graph
        self.annotations = self.query_state.annotations
        # Tracks relationship paths resolved during annotation context,
        # used later by QueryBuilder.having() to mark JOIN nodes
        # as used_in_filter for JOIN type promotion (LEFT → INNER)
        self._recently_resolved_paths: list[str] = []

    def resolve_to_column(
        self,
        key: str,
        context: ResolverContext = ResolverContext(),
    ) -> FieldResolution:
        """Resolve Django-style field key to SQLAlchemy column with JOIN registration.

        Transforms a field key (e.g., "category__name__icontains") into a SQLAlchemy
        column reference, registering any necessary JOINs with the query's JOIN graph.
        The resolution process handles relationship traversal, lookup extraction, and
        special cases like pk alias and annotations.

        Resolution follows two paths:

        Simple Path (no relationships):
        - Parse key to extract field name and lookup
        - Resolve field on root model (check annotations first, then model attributes)
        - Return FieldResolution with column and no join_node

        Relationship Path (with relationships):
        - Parse key to extract relationship path, field name, and lookup
        - Register JOINs for relationship path via JOIN graph
        - Resolve terminal field on joined model's alias
        - Return FieldResolution with column and join_node reference

        The context parameter controls JOIN optimization behavior through flags like
        mark_filter, in_or_context, and in_negation. These flags are propagated to
        the JOIN graph to ensure correct JOIN type selection.

        Args:
            key: Django-style field key with optional lookup. Examples: "status",
                "price__gte", "category__name__icontains", "user__books__title".
            context: ResolverContext controlling JOIN registration and optimization.
                    Defaults to empty context (no special behavior).

        Returns:
            FieldResolution containing resolved column, lookup operator, and optional
            JOIN metadata. The column is ready for use in SQLAlchemy expressions.

        Raises:
            ModelAttributeError: Field path references non-existent attribute on
                                model or related model. Includes model name and
                                attribute name in error message.

        Example:
            >>> # Simple field resolution
            >>> resolver.resolve_to_column("status")
            >>> # Returns: FieldResolution(field_path="", field_name="status",
            >>> #   lookup="exact", column=Product.status, join_node=None)
            >>>
            >>> # Field with lookup
            >>> resolver.resolve_to_column("price__gte")
            >>> # Returns: FieldResolution(field_path="", field_name="price",
            >>> #   lookup="gte", column=Product.price, join_node=None)
            >>>
            >>> # Relationship field with filter context
            >>> context = ResolverContext(mark_filter=True)
            >>> resolver.resolve_to_column("category__name", context=context)
            >>> # Returns: FieldResolution(field_path="category", field_name="name",
            >>> #   lookup="exact", column=category_alias.name,
            >>> #   join_node=JoinNode(path="category", used_in_filter=True, ...))
            >>> # Side effect: Registers JOIN with categories, marked as filter
            >>>
            >>> # Nested relationships with OR context
            >>> context = ResolverContext(mark_filter=True, in_or_context=True)
            >>> resolver.resolve_to_column("supplier__country__code", context=context)
            >>> # Returns: FieldResolution(field_path="supplier__country",
            >>> #   field_name="code", lookup="exact", column=country_alias.code,
            >>> #   join_node=JoinNode(path="supplier__country", ...))
            >>> # Side effect: Registers JOINs, marked with OR context (stays LEFT)
            >>>
            >>> # Primary key alias
            >>> resolver.resolve_to_column("pk")
            >>> # Returns: FieldResolution(field_path="", field_name="pk",
            >>> #   lookup="exact", column=Product.id, join_node=None)>>>
            >>> # Annotation with lookup
            >>> resolver.resolve_to_column("total_price__gte")
            >>> # Returns: FieldResolution(field_path="", field_name="total_price",
            >>> #   lookup="gte", column=<annotation_expression>, join_node=None)>>>
            >>> # Direct relation filter (no terminal field)
            >>> resolver.resolve_to_column("building")
            >>> # Returns: FieldResolution(field_path="building", field_name="",
            >>> #   lookup="exact", column=building_alias, join_node=JoinNode(...))

        Note:
            Performance is O(1) for simple fields, O(n) for n-level relationship
            traversal. Each relationship level requires model introspection and
            JOIN graph interaction.

            The resolver tracks annotation paths when in_annotation=True context,
            enabling JOIN graph optimization for annotate() operations. Call
            pop_resolved_paths() to retrieve and clear tracked paths.

            JOIN registration is idempotent - multiple resolutions of the same path
            return the same JoinNode without creating duplicate JOINs.
        """
        parsed = self.parse_field_key(key)

        # Simple field on root model — no joins needed
        if parsed.is_simple:
            column = self._resolve_simple_field(parsed.field_name)
            return FieldResolution(
                field_path="",
                field_name=parsed.field_name,
                lookup=parsed.lookup,
                column=column,
                join_node=None,
            )

        # Relationship traversal required
        field_path = "__".join(parsed.relationship_parts)
        node = self.join_graph.add_node(
            path=field_path,
            used_in_filter=context.mark_filter,
            used_in_or_context=context.in_or_context,
            used_in_negation=context.in_negation,
            used_in_order_by=context.in_order_by,
            used_in_annotation=context.in_annotation,
            has_nullable_check=context.has_nullable_check,
        )
        if context.in_annotation:
            # Store path so QueryBuilder can map annotation → relationship paths
            # via pop_resolved_paths(). When HAVING references this annotation,
            # _resolve_having_condition() retrieves these paths to mark the
            # JOIN node as used_in_filter, promoting it from LEFT to INNER JOIN.
            self._recently_resolved_paths.append(field_path)

        # Resolve terminal field on the joined model
        if parsed.field_name:
            column = self._resolve_field_on_node(node, parsed.field_name)
        else:
            # Direct relationship filter (e.g., filter(building=instance))
            column = node.alias

        return FieldResolution(
            field_path=field_path,
            field_name=parsed.field_name,
            lookup=parsed.lookup,
            column=column,
            join_node=node,
        )

    def parse_field_key(self, key: str) -> ParsedFieldKey:
        """Parse Django-style field key into relationship path, field name, and lookup.

        Decomposes a field key using model introspection to distinguish relationships
        from fields and lookup operators. This is pure parsing with no side effects -
        no JOINs are created, no columns are resolved.

        The parsing algorithm walks through key parts left-to-right, using model
        introspection to identify relationships. When a non-relationship part is
        found, it's treated as the terminal field, and remaining parts become the
        lookup operator.

        Special cases handled:
        - Annotations: Recognized before relationship traversal
        - Primary key alias: "pk" treated as field name
        - Direct relation filters: All parts are relationships, no terminal field
        - Compound lookups: Multiple "__" parts after field (e.g., "__not__in")

        Args:
            key: Django-style field key. Examples: "status", "price__gte",
                "category__name__icontains", "user__books__title".

        Returns:
            ParsedFieldKey containing relationship_parts, field_name, lookup, and
            is_simple flag. Ready for use in resolve_to_column().

        Example:
            >>> # Simple field (no relationships)
            >>> resolver.parse_field_key("status")
            >>> # Returns: ParsedFieldKey(relationship_parts=[], field_name="status",
            >>> #   lookup="exact", is_simple=True)
            >>>
            >>> # Field with lookup
            >>> resolver.parse_field_key("price__gte")
            >>> # Returns: ParsedFieldKey(relationship_parts=[], field_name="price",
            >>> #   lookup="gte", is_simple=True)
            >>>
            >>> # Single relationship
            >>> resolver.parse_field_key("building__name")
            >>> # Returns: ParsedFieldKey(relationship_parts=["building"],
            >>> #   field_name="name", lookup="exact", is_simple=False)
            >>>
            >>> # Nested relationships with lookup
            >>> resolver.parse_field_key("user__books__title__icontains")
            >>> # Returns: ParsedFieldKey(relationship_parts=["user", "books"],
            >>> #   field_name="title", lookup="icontains", is_simple=False)
            >>>
            >>> # Direct relation filter (no terminal field)
            >>> resolver.parse_field_key("building")
            >>> # Returns: ParsedFieldKey(relationship_parts=["building"],
            >>> #   field_name="", lookup="exact", is_simple=False)>>>
            >>> # Annotation with lookup
            >>> resolver.parse_field_key("total_price__gte")
            >>> # Returns: ParsedFieldKey(relationship_parts=[],
            >>> #   field_name="total_price", lookup="gte", is_simple=True)
            >>>
            >>> # Compound lookup
            >>> resolver.parse_field_key("tags__not__in")
            >>> # Returns: ParsedFieldKey(relationship_parts=[], field_name="tags",
            >>> #   lookup="not__in", is_simple=True)

        Note:
            Performance is O(n) where n is the number of parts in the key. Each part
            requires model introspection via getattr() and relationship checking.

            The is_simple flag enables fast-path optimization in resolve_to_column() -
            simple fields skip JOIN graph interaction entirely.
            This method is pure and stateless - it can be called multiple times with
            the same key without side effects. The model introspection is read-only.
        """
        if "__" not in key:
            return ParsedFieldKey(
                relationship_parts=[],
                field_name=key,
                lookup="exact",
                is_simple=True,
            )

        parts = key.split("__")
        if parts[0] == "pk":
            return ParsedFieldKey(
                relationship_parts=[],
                field_name="pk",
                lookup="__".join(parts[1:]) or "exact",
                is_simple=True,
            )

        # Check if the first part is an annotated field (e.g., "quota_max__gte")
        if parts[0] in self.annotations:
            lookup = "__".join(parts[1:]) or "exact"
            return ParsedFieldKey(
                relationship_parts=[],
                field_name=parts[0],
                lookup=lookup,
                is_simple=True,
            )

        current_model = self.model
        relationship_parts: list[str] = []

        for i, part in enumerate(parts):
            attr = getattr(current_model, part, None)

            if attr is None:
                # Unknown part — treat as field + lookup
                if relationship_parts:
                    field_name = part
                    lookup = "__".join(parts[i + 1 :]) or "exact"
                    return ParsedFieldKey(
                        relationship_parts=relationship_parts,
                        field_name=field_name,
                        lookup=lookup,
                        is_simple=False,
                    )
                # No relationships found — treat entire key as field
                return ParsedFieldKey(
                    relationship_parts=[],
                    field_name=key,
                    lookup="exact",
                    is_simple=True,
                )

            if self._is_relationship(attr):
                relationship_parts.append(part)
                current_model = attr.property.mapper.class_
            else:
                # Terminal column found
                lookup = "__".join(parts[i + 1 :]) or "exact"
                return ParsedFieldKey(
                    relationship_parts=relationship_parts,
                    field_name=part,
                    lookup=lookup,
                    is_simple=len(relationship_parts) == 0,
                )

        # All parts were relationships (direct relation filter)
        return ParsedFieldKey(
            relationship_parts=relationship_parts,
            field_name="",
            lookup="exact",
            is_simple=False,
        )

    def _resolve_simple_field(self, field_name: str):
        """Resolve field on root model, checking annotations first then model attributes.

        Handles three cases:
        1. Annotation: Return annotation expression directly
        2. Primary key alias: Resolve to actual pk column
        3. Model attribute: Return model's column attribute

        Args:
            field_name: Field name to resolve on root model.

        Returns:
            SQLAlchemy column, annotation expression, or model attribute.

        Raises:
            ModelAttributeError: Field doesn't exist on model.

        Note:
            Annotations take precedence over model attributes - if an annotation
            shadows a model field, the annotation is returned.
        """
        if field_name in self.annotations:
            return self.annotations[field_name]

        # Handle PK alias
        if field_name == self.PK_FIELD_ALIAS:
            from sqlalchemy import inspect as sa_inspect, tuple_

            inspection: Any = sa_inspect(self.model)
            mapper = inspection.mapper if inspection.is_aliased_class else inspection
            primary_key = mapper.primary_key
            if len(primary_key) == 1:
                return getattr(
                    self.model, mapper.get_property_by_column(primary_key[0]).key
                )
            return tuple_(
                *(
                    getattr(self.model, mapper.get_property_by_column(col).key)
                    for col in primary_key
                )
            )

        if not hasattr(self.model, field_name):
            raise ModelAttributeError(
                f"{self.model.__name__} has no attribute '{field_name}'"
            )
        return getattr(self.model, field_name)

    @staticmethod
    def _resolve_field_on_node(node: JoinNode, field_name: str):
        """Resolve field on joined model's alias, handling pk alias and validation.

        Resolves a field name to a column on a JoinNode's aliased model. Handles
        the pk alias by resolving to the actual primary key column name, and
        validates that the resolved attribute is a SQLAlchemy column.

        Args:
            node: JoinNode containing the aliased model to resolve field on.
            field_name: Field name to resolve, or "pk" for primary key.

        Returns:
            SQLAlchemy InstrumentedAttribute from the aliased model.

        Raises:
            ModelAttributeError: Field doesn't exist on model or isn't a column.

        Note:
            Uses node.alias which is a SQLAlchemy AliasedClass, not the raw model.
            This ensures column references use the correct alias in generated SQL.
        """
        if field_name == FieldResolver.PK_FIELD_ALIAS:
            from sqlalchemy import inspect as sa_inspect, tuple_

            mapper = sa_inspect(node.alias).mapper
            primary_key = mapper.primary_key
            if len(primary_key) == 1:
                return getattr(
                    node.alias, mapper.get_property_by_column(primary_key[0]).key
                )
            return tuple_(
                *(
                    getattr(node.alias, mapper.get_property_by_column(col).key)
                    for col in primary_key
                )
            )

        attr = getattr(node.alias, field_name, None)

        if attr is None:
            model_class = node.alias._aliased_insp.mapper.class_
            raise ModelAttributeError(
                f"{model_class.__name__} has no attribute '{field_name}'"
            )

        if not isinstance(attr, QueryableAttribute):
            model_class = node.alias._aliased_insp.mapper.class_
            raise ModelAttributeError(
                f"'{field_name}' on {model_class.__name__} is not a "
                f"SQLAlchemy column (got {type(attr).__name__})"
            )

        return attr

    @staticmethod
    def _is_relationship(attr) -> bool:
        """Check if model attribute is a SQLAlchemy relationship.

        Uses SQLAlchemy's RelationshipProperty to identify relationship attributes
        versus column attributes. This distinction is critical for parse_field_key()
        to correctly identify relationship traversal paths.

        Args:
            attr: Model attribute to check, typically from getattr(model, name).

        Returns:
            True if attribute is a relationship, False otherwise.

        Example:
            >>> _is_relationship(Product.category)  # ForeignKey relationship
            >>> # Returns: True
            >>>
            >>> _is_relationship(Product.name)  # Column attribute
            >>> # Returns: False
        Note:
            This is a static method because it doesn't need instance state - it's
            pure model introspection logic used during parsing.
        """
        return hasattr(attr, "property") and isinstance(
            attr.property, RelationshipProperty
        )

    @staticmethod
    def _resolve_pk(model_class):
        """Resolve primary key column for a model class.

        Extracts the primary key column from a SQLAlchemy model's table metadata.
        Only supports single-column primary keys - composite keys must be referenced
        explicitly by field name.

        Args:
            model_class: SQLAlchemy model class to resolve pk for.

        Returns:
            SQLAlchemy Column object representing the primary key.

        Raises:
            ModelAttributeError: Model has composite primary key (multiple columns).

        Example:
            >>> _resolve_pk(Product)
            >>> # Returns: Column('id', Integer(), primary_key=True)
            >>>
            >>> _resolve_pk(OrderItem)  # Composite pk: (order_id, product_id)
            >>> # Raises: ModelAttributeError("OrderItem has composite primary key...")
        Note:
            The pk alias is a convenience feature matching Django ORM behavior.
            For composite keys, users must specify the exact field name.
        """
        pk_cols = model_class.__table__.primary_key.columns
        if len(pk_cols) == 1:
            return list(pk_cols)[0]
        raise ModelAttributeError(
            f"{model_class.__name__} has composite primary key, use explicit field name"
        )

    def pop_resolved_paths(self) -> list[str]:
        """Return and clear recently resolved relationship paths from annotations.

        Retrieves relationship paths that were resolved during annotation operations
        (in_annotation=True context), then clears the tracking list. These paths are
        stored in QueryState.annotation_paths to enable JOIN promotion when HAVING
        clauses reference the annotations.

        Returns:
            List of relationship paths (e.g., ["category", "author__publisher"])
            resolved since last pop. Empty list if no paths were tracked.

        Example:
            >>> # During annotate(category_name=F("category__name"))
            >>> resolver.resolve_to_column("category__name",
            ...     context=ResolverContext(in_annotation=True))
            >>> paths = resolver.pop_resolved_paths()
            >>> # Returns: ["category"]
            >>>
            >>> # Later in having(category_name__icontains="tech")
            >>> # Uses stored path to mark join node as used_in_filter
            >>> # promoting LEFT JOIN to INNER JOIN

        Note:
            Called by QueryBuilder after annotation resolution to populate
            QueryState.annotation_paths. When HAVING references an annotation,
            _resolve_having_condition() uses these paths to locate and mark
            JOIN nodes as used_in_filter for query optimization.
        """
        paths = self._recently_resolved_paths.copy()
        self._recently_resolved_paths.clear()
        return paths
