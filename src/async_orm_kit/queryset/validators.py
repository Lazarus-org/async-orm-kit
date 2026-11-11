from async_orm_kit.exceptions import QueryStateError
from async_orm_kit.queryset.state import QueryState


class QuerySetValidator:
    """Validates QuerySet method calls and state transitions for correctness.

    Enforces Django-style ORM constraints by validating that QuerySet methods are
    called in compatible combinations. Prevents invalid state transitions like calling
    select_related() after values() (which returns dicts, not model instances), or
    using HAVING without aggregates. Follows Single Responsibility Principle by
    separating validation logic from QuerySet's query building responsibilities.

    Attributes:
        _state: QueryState instance containing current query configuration to validate.
    Example:
        >>> # QuerySetValidator is internal - users see validation errors via QuerySet
        >>> from your_app.models import DBUser
        >>>
        >>> # Invalid: select_related after values
        >>> try:
        ...     DBUser.objects.values("username").select_related("profile")
        ... except QueryStateError as e:
        ...     print(e)  # "Cannot call select_related() after values()..."
        >>>
        >>> # Invalid: order_by field not in values selection
        >>> try:
        ...     User.objects.values("username").order_by("email")
        ... except QueryStateError as e:
        ...     print(e)  # "Cannot order by 'email' - not in values() selection..."
        >>>
        >>> # Invalid: having without aggregates
        >>> try:
        ...     User.objects.having(count__gte=5)
        ... except QueryStateError as e:
        ...     print(e)  # "having() requires at least one aggregate annotation..."
    Note:
        Performance: All validations are O(1) or O(n) where n is number of fields,
        executed in-memory before database queries. Validation overhead is negligible
        compared to query execution time.
    """

    __slots__ = ("_state",)

    def __init__(self, state: QueryState):
        """Initialize validator with QueryState to validate.

        Args:
            state: QueryState instance containing query configuration to validate.
        """
        self._state = state

    def validate_requires_model_instances(self, method_name: str) -> None:
        """Validate that QuerySet returns model instances, not dicts or tuples.

        Methods like select_related(), prefetch_related(), and only() operate on
        model instances by loading related objects or deferring fields. These methods
        are incompatible with values() and values_list() which return dictionaries
        and tuples respectively. This validation prevents nonsensical combinations
        like trying to eager-load relationships on dictionary results.

        Args:
            method_name: Name of method being called (for error message).

        Raises:
            QueryStateError: If values() or values_list() was called before this method,
                           making the operation invalid.

        Example:
            >>> # Valid: select_related on model instances
            >>> User.objects.select_related("profile").all()
            >>>
            >>> # Invalid: select_related after values
            >>> User.objects.values("username").select_related("profile")
            >>> # Raises: QueryStateError("Cannot call select_related() after values()...")
            >>>
            >>> # Invalid: only after values_list
            >>> User.objects.values_list("id").only("username")
            >>> # Raises: QueryStateError("Cannot call only() after values_list()...")
        Note:
            Performance: O(1) - simple attribute checks. Called before query building,
            preventing invalid queries from reaching the database.
        """
        if self._state.values_select is not None:
            raise QueryStateError(
                f"Cannot call {method_name}() after values(). "
                f"values() returns dictionaries, not model instances."
            )
        if self._state.values_list_select is not None:
            raise QueryStateError(
                f"Cannot call {method_name}() after values_list(). "
                f"values_list() returns tuples, not model instances."
            )

    def validate_not_deferred_loading(self, method_name: str) -> None:
        """Validate that deferred loading (only) is not active.

        The only() method implements deferred loading by selecting specific fields
        and deferring others. Methods like values() and values_list() have their own
        field selection semantics that conflict with only()'s approach. This validation
        prevents combining these incompatible field selection strategies.

        Args:
            method_name: Name of method being called (for error message).

        Raises:
            QueryStateError: If only() was called before this method, creating
                           conflicting field selection semantics.

        Example:
            >>> # Valid: values without only
            >>> User.objects.values("username", "email")
            >>>
            >>> # Invalid: values after only
            >>> User.objects.only("username").values("email")
            >>> # Raises: QueryStateError("Cannot call values() after only()...")
            >>>
            >>> # Invalid: values_list after only
            >>> User.objects.only("id", "username").values_list("email")
            >>> # Raises: QueryStateError("Cannot call values_list() after only()...")

        Note:
            Performance: O(1) - simple boolean check on only_fields set. Prevents
            ambiguous field selection before query compilation.
        """
        if self._state.only_fields or self._state.deferred_fields:
            method = "only" if self._state.only_fields else "defer"
            raise QueryStateError(
                f"Cannot call {method_name}() after {method}(). "
                "These methods have conflicting field selection semantics."
            )

    def validate_order_by_fields(self, fields: tuple) -> None:
        """Validate that order_by fields exist in current selection mode.

        When using values() or values_list() to project specific fields, ORDER BY
        can only reference fields that are in the SELECT clause or are annotations.
        This is a SQL requirement - you cannot order by fields that aren't selected.
        When returning model instances (no values/values_list), all model fields are
        available for ordering.

        Args:
            fields: Tuple of field names to order by. May include "-" prefix for
                   descending order (e.g., "-created_at").

        Raises:
            QueryStateError: If any order_by field is not in the values/values_list
                           selection or annotations, making the SQL query invalid.

        Example:
            >>> # Valid: order by selected field
            >>> User.objects.values("username", "email").order_by("username")
            >>>
            >>> # Valid: order by annotation
            >>> User.objects.values("username").annotate(
            ...     post_count=Count("posts")
            ... ).order_by("post_count")
            >>>
            >>> # Invalid: order by non-selected field
            >>> User.objects.values("username").order_by("email")
            >>> # Raises: QueryStateError("Cannot order by 'email' - not in values() selection...")
            >>>
            >>> # Valid: descending order with prefix
            >>> User.objects.values("username", "created_at").order_by("-created_at")

        Note:
            Performance: O(n*m) where n is number of order_by fields and m is number
            of selected fields. Typically small numbers, negligible overhead. Strips
            "-" prefix for descending order before validation.
        """
        if self._state.values_select is None and self._state.values_list_select is None:
            return  # No restriction when returning model instances

        selected_fields = self._get_selected_fields()
        annotation_names = set(self._state.annotations.keys())
        allowed_fields = selected_fields | annotation_names

        for field in fields:
            if not isinstance(field, str):
                raise QueryStateError(
                    "Expression-based ordering is only supported when returning "
                    "model instances; annotate the expression and select its alias "
                    "when using values() or values_list()."
                )
            if not field or field == "-" or field.startswith("--"):
                raise QueryStateError(f"Invalid order_by field {field!r}.")
            field_name = field[1:] if field.startswith("-") else field
            if field_name not in allowed_fields:
                mode = (
                    "values()"
                    if self._state.values_select is not None
                    else "values_list()"
                )
                raise QueryStateError(
                    f"Cannot order by '{field_name}' - not in {mode} selection. "
                    f"Available fields: {sorted(allowed_fields)}"
                )

    def validate_distinct_fields(self, fields: tuple) -> None:
        """Validate that DISTINCT ON fields exist in current selection mode.

        PostgreSQL's DISTINCT ON requires that distinct fields appear in the SELECT
        clause. When using values() or values_list() to project specific fields,
        DISTINCT ON can only reference those selected fields. Plain DISTINCT (without
        fields) is always valid. When returning model instances, all fields are available.

        Args:
            fields: Tuple of field names for DISTINCT ON. Empty tuple means plain
                   DISTINCT (no field-specific deduplication).

        Raises:
            QueryStateError: If any DISTINCT ON field is not in the values/values_list
                           selection, making the PostgreSQL query invalid.

        Example:
            >>> # Valid: plain DISTINCT
            >>> User.objects.values("username").distinct()
            >>>
            >>> # Valid: DISTINCT ON selected field
            >>> User.objects.values("username", "email").distinct("username")
            >>>
            >>> # Invalid: DISTINCT ON non-selected field
            >>> User.objects.values("username").distinct("email")
            >>> # Raises: QueryStateError("distinct() field 'email' must be in values() selection...")
            >>>
            >>> # Valid: DISTINCT ON with model instances
            >>> User.objects.distinct("username")  # All fields available

        Note:
            Performance: O(n*m) where n is number of distinct fields and m is number
            of selected fields. Empty fields tuple (plain DISTINCT) returns immediately.
            PostgreSQL-specific feature - other databases may not support DISTINCT ON.
        """
        if not fields:
            return  # DISTINCT without fields is always valid

        if self._state.values_select is None and self._state.values_list_select is None:
            return  # No restriction when returning model instances

        selected_fields = self._get_selected_fields()

        for field in fields:
            if field not in selected_fields:
                mode = "values()" if self._state.values_select else "values_list()"
                raise QueryStateError(
                    f"distinct() field '{field}' must be in {mode} selection. "
                    f"Available: {sorted(selected_fields)}"
                )

    def validate_values_list_flat(self, fields: tuple, flat: bool) -> None:
        """Validate that flat parameter is used correctly with values_list.

        The flat=True parameter flattens values_list results from list of tuples to
        list of scalar values. This only makes sense when selecting exactly one field,
        as multiple fields cannot be flattened to scalars. This validation prevents
        ambiguous flattening like trying to flatten (id, username) tuples.

        Args:
            fields: Tuple of field names being selected.
            flat: Whether to flatten results to scalar values.

        Raises:
            QueryStateError: If flat=True with multiple fields, creating ambiguous
                           flattening behavior.

        Example:
            >>> # Valid: flat with single field
            >>> ids = User.objects.values_list("id", flat=True)
            >>> # Returns: [1, 2, 3] instead of [(1,), (2,), (3,)]
            >>>
            >>> # Valid: flat=False with multiple fields
            >>> pairs = User.objects.values_list("id", "username", flat=False)
            >>> # Returns: [(1, "alice"), (2, "bob")]
            >>>
            >>> # Invalid: flat with multiple fields
            >>> User.objects.values_list("id", "username", flat=True)
            >>> # Raises: QueryStateError("values_list() with flat=True requires exactly one field...")

        Note:
            Performance: O(1) - simple length check. Called before query execution
            to prevent invalid result transformation.
        """
        if type(flat) is not bool:
            raise TypeError("flat must be a bool.")
        if flat and len(fields) != 1:
            raise QueryStateError(
                "values_list() with flat=True requires exactly one field, "
                f"got {len(fields)} fields"
            )

    def validate_having_requires_aggregates(self) -> None:
        """Validate that HAVING clause is used with aggregate functions.

        SQL's HAVING clause filters grouped results after aggregation, requiring a
        GROUP BY clause (created by aggregates). Using HAVING without aggregates is
        semantically incorrect - use WHERE/filter() instead for row-level filtering.
        This validation enforces Django-style ORM behavior where HAVING only makes
        sense with annotate() containing aggregate functions like Count, Sum, Avg.

        Raises:
            QueryStateError: If having() is called without any aggregate annotations,
                           making the HAVING clause semantically invalid.

        Example:
            >>> # Valid: having with aggregates
            >>> User.objects.annotate(
            ...     post_count=Count("posts")
            ... ).having(post_count__gte=5)
            >>>
            >>> # Valid: multiple aggregates
            >>> User.objects.annotate(
            ...     total_views=Sum("posts__views"),
            ...     avg_rating=Avg("posts__rating")
            ... ).having(total_views__gte=1000, avg_rating__gte=4.0)
            >>>
            >>> # Invalid: having without aggregates
            >>> User.objects.having(username__startswith="a")
            >>> # Raises: QueryStateError("having() requires at least one aggregate annotation...")
            >>>
            >>> # Invalid: having with non-aggregate annotation
            >>> User.objects.annotate(
            ...     full_name=Concat("first_name", Value(" "), "last_name")
            ... ).having(full_name__icontains="smith")
            >>> # Raises: QueryStateError (no aggregates, so no GROUP BY)

        Note:
            Performance: O(1) - simple boolean check on has_aggregates flag. Prevents
            invalid SQL generation before query compilation. Use filter() instead of
            having() for row-level filtering without aggregates.
        """
        if not self._state.has_aggregates:
            raise QueryStateError(
                "having() requires at least one aggregate annotation. "
                "Call .annotate(total=Sum('amount')) before .having(total__gte=100). "
                f"Current annotations: {list(self._state.annotations.keys()) or 'none'}"
            )

    def _get_selected_fields(self) -> set[str]:
        """Extract field names from current values/values_list selection.

        Helper method that unpacks field names from the (field_name, column) tuples
        stored in values_select or values_list_select. Returns empty set if neither
        projection mode is active (returning model instances).

        Returns:
            Set of field names currently selected, or empty set if no projection.

        Example:
            >>> # Internal usage in validation methods
            >>> validator = QuerySetValidator(state)
            >>> fields = validator._get_selected_fields()
            >>> # Returns: {"username", "email"} if values("username", "email")
            >>> # Returns: set() if no values/values_list
        """
        if self._state.values_select is not None:
            # Unpack field names from (field_name, column) tuples
            return {field_name for field_name, col in self._state.values_select}
        if self._state.values_list_select is not None:
            # Unpack field names from (field_name, column) tuples
            return {field_name for field_name, col in self._state.values_list_select}
        return set()
