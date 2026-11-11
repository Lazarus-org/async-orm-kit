"""QuerySet exception hierarchy for ORM operations.

This module defines a comprehensive exception hierarchy for handling errors
during QuerySet construction, compilation, and execution. All exceptions inherit
from QuerySetError to allow catching all ORM-related errors.

The exception hierarchy is designed to provide specific error types for different
failure scenarios, enabling precise error handling and debugging.
"""


class QuerySetError(Exception):
    """Base exception for all QuerySet-related errors.

    This is the root exception class for the custom ORM. All QuerySet operations
    that fail will raise this exception or one of its subclasses. Use this for
    broad exception handling when you want to catch any ORM error.

    Example:
        >>> try:
        ...     qs = User.objects.filter(invalid_field="value")
        ... except QuerySetError as e:
        ...     logger.error(f"QuerySet operation failed: {e}")

    Note:
        Catch specific subclasses when you need granular error handling.
        Use this base class only for generic error handling or logging.
    """

    pass


class ModelAttributeError(QuerySetError):
    """Raised when accessing or filtering by non-existent model attributes.

    This exception occurs when you reference a field or attribute that doesn't
    exist on the model class. It helps catch typos and prevents silent failures
    when building queries with invalid field names.

    Example:
        >>> # Model has 'username' but you typed 'user_name'
        >>> User.objects.filter(user_name="john")
        ModelAttributeError: Model 'User' has no attribute 'user_name'

        >>> # Correct usage
        >>> User.objects.filter(username="john")

    """

    pass


class RelationshipError(QuerySetError):
    """Raised when relationship navigation or joining fails.

    This exception occurs when attempting to traverse relationships that don't
    exist, are misconfigured, or when the relationship path is invalid. Common
    causes include missing foreign keys, incorrect relationship names, or
    attempting to join through non-relationship fields.

    Raises:
        RelationshipError: When select_related() or prefetch_related() references
            invalid relationships, or when filter() uses invalid relationship paths.

    Example:
        >>> # Wrong: 'author' relationship doesn't exist on Post
        >>> Post.objects.select_related("author")
        RelationshipError: Model 'Post' has no relationship 'author'

        >>> # Correct: use the actual relationship name
        >>> Post.objects.select_related("user")

        >>> # Wrong: trying to join through a non-relationship field
        >>> Post.objects.filter(created_at__user__name="john")
        RelationshipError: Field 'created_at' is not a relationship

    Note:
        Performance: Raised at query construction time (O(1)).
        Validate relationship paths early to avoid runtime failures.
    """

    pass


class FilterError(QuerySetError):
    """Raised when filter operations fail due to invalid syntax or values.

    This is a general exception for filter-related errors that don't fit into
    more specific categories. It covers issues like invalid filter syntax,
    incompatible value types, or malformed filter expressions.

    Example:
        >>> # Wrong: invalid filter syntax
        >>> User.objects.filter(age__between=25)  # missing second value
        FilterError: 'between' lookup requires two values

        >>> # Correct
        >>> User.objects.filter(age__between=(25, 35))

    Note:
        This is a base class for more specific filter errors like
        InvalidLookupError and InvalidRelationshipValueError.
    """

    pass


class DatabaseError(QuerySetError):
    """Raised when database operations fail during query execution.

    This exception wraps database-level errors that occur when executing queries,
    such as connection failures, constraint violations, or SQL syntax errors.
    It provides a consistent interface for handling database failures regardless
    of the underlying database driver.

    Raises:
        DatabaseError: When query execution fails due to database issues.

    Example:
        >>> try:
        ...     user = User.objects.create(email="duplicate@example.com")
        ... except DatabaseError as e:
        ...     if "unique constraint" in str(e).lower():
        ...         logger.warning("Duplicate email attempted")
        ...     else:
        ...         raise

    Note:
        Performance: Only raised at query execution time, not construction.
        This indicates a runtime issue, not a code error.
        Consider implementing retry logic for transient database failures.
    """

    def __init__(
        self, message: str = "Database error", cause: Exception | None = None, *args
    ):
        self.message = message
        self.cause = cause
        super().__init__(message, *args)


class InvalidLookupError(FilterError):
    """Raised when an unsupported or forbidden lookup operator is used.

    This exception occurs when you use a lookup that either doesn't exist or
    isn't supported for the field type you're filtering on. It helps prevent
    queries that would fail at the database level or produce unexpected results.

    Args:
        message: The error message.
        lookup: The invalid lookup name that was attempted.
        field_name: The field on which the lookup was attempted.

    Raises:
        InvalidLookupError: When using non-existent lookups or incompatible
            lookup-field combinations.

    Example:
        >>> # Wrong: 'contains' doesn't work on integer fields
        >>> User.objects.filter(age__contains=5)
        InvalidLookupError: Lookup 'contains' not supported for IntegerField

        >>> # Wrong: typo in lookup name
        >>> User.objects.filter(name__icontain="john")
        InvalidLookupError: Unknown lookup 'icontain'. Did you mean 'icontains'?

        >>> # Correct
        >>> User.objects.filter(name__icontains="john")
        >>> User.objects.filter(age__gte=18)

    Note:
        Performance: Raised at query construction time (O(1)).
        Valid lookups depend on field type: strings support 'contains',
        'icontains', 'startswith'; numbers support 'gt', 'gte', 'lt', 'lte';
        dates support 'year', 'month', 'day', etc.
    """

    def __init__(self, message: str, lookup: str, field_name: str, *args):
        self.lookup = lookup
        self.field_name = field_name
        super().__init__(message, *args)


class InvalidRelationshipValueError(FilterError):
    """Raised when an invalid value is passed to a relationship comparison.

    This exception occurs when filtering by relationships with incorrect value
    types. The most common mistake is passing a model class instead of a model
    instance, or passing a non-model object where a relationship is expected.

    Args:
        message: The error message.
        value: The invalid value that was passed.
        field_name: The relationship field being filtered.

    Raises:
        InvalidRelationshipValueError: When filtering relationships with
            model classes, primitives, or other invalid types.

    Example:
        >>> # Wrong: passing a class instead of an instance
        >>> Floor.objects.filter(building=DBBuilding)
        InvalidRelationshipValueError: Expected DBBuilding instance, got type
        >>> # Wrong: passing a primitive value
        >>> Floor.objects.filter(building=123)
        InvalidRelationshipValueError: Expected DBBuilding instance, got int

        >>> # Correct: pass a mapped instance
        >>> building = await DBBuilding.objects.get(id=1)
        >>> floors = await Floor.objects.filter(building=building)

        >>> # Also correct: filter by relationship's primary key
        >>> floors = await Floor.objects.filter(building_id=1)

    Note:
        Performance: Raised at query construction time (O(1)).
        To filter by ID without fetching the instance, use the foreign key
        field directly (e.g., 'building_id' instead of 'building').
    """

    def __init__(self, message: str, value: str, field_name: str, *args):
        self.value = value
        self.field_name = field_name
        super().__init__(message, *args)


class QueryStateError(QuerySetError):
    """Raised when queryset methods are called in an invalid state or order.

    This exception enforces the correct sequencing of QuerySet operations.
    Certain methods are incompatible or must be called in a specific order.
    This prevents building queries that would produce unexpected results or
    fail at execution time.

    Raises:
        QueryStateError: When methods are called in an incompatible order or
            when the QuerySet is in a state that doesn't support the operation.

    Example:
        >>> # Wrong: select_related() after values() - can't join after projection
        >>> User.objects.values("name").select_related("profile")
        QueryStateError: Cannot call select_related() after values()

        >>> # Wrong: values() after only() - conflicting field selections
        >>> User.objects.only("name").values("email")
        QueryStateError: Cannot call values() after only()

        >>> # Wrong: order_by() with field not in values() selection
        >>> User.objects.values("name").order_by("email")
        QueryStateError: Cannot order by 'email' - not in values() selection

        >>> # Wrong: having() without aggregates
        >>> User.objects.having(age__gt=18)
        QueryStateError: Cannot use having() without aggregate functions

        >>> # Correct: proper method ordering
        >>> User.objects.select_related("profile").values("name", "profile__bio")
        >>> User.objects.values("name").order_by("name")
        >>> User.objects.annotate(post_count=Count("posts")).having(post_count__gt=5)

    Note:
        Performance: Raised at query construction time (O(1)).
        Method order matters: select_related/prefetch_related → filter/exclude →
        annotate → values/only → order_by → having → limit/offset.
        Not all combinations are invalid, but some fundamentally conflict.
    """

    pass


class ExpressionTypeError(QuerySetError):
    """Raised when an invalid type is passed where a string or expression is expected.

    This exception occurs when you pass an expression object (like F() or Q())
    in a context that requires additional metadata, such as ordering direction.
    It helps catch cases where expressions need to be wrapped with .asc() or
    .desc() for proper SQL generation.

    Raises:
        ExpressionTypeError: When passing bare expressions without required
            metadata, or when using incompatible types in expression contexts.

    Example:
        >>> # Wrong: bare F expression in order_by() - missing direction
        >>> Product.objects.order_by(F("price") * F("quantity"))
        ExpressionTypeError: Expression in order_by() must specify .asc() or .desc()

        >>> # Wrong: passing a non-string, non-expression type
        >>> User.objects.order_by(123)
        ExpressionTypeError: order_by() expects str or Expression, got int

        >>> # Correct: wrap expressions with ordering direction
        >>> Product.objects.order_by((F("price") * F("quantity")).desc())

        >>> # Correct: use string references for simple cases
        >>> User.objects.order_by("created_at", "-updated_at")

        >>> # Correct: expressions in annotate() don't need direction
        >>> Product.objects.annotate(total=F("price") * F("quantity"))

    Note:
        Performance: Raised at query construction time (O(1)).
        String field references are simpler and preferred for basic ordering.
        Use F() expressions when you need computed values or complex operations.
        Always call .asc() or .desc() on expressions used in order_by().
    """

    pass


class ObjectDoesNotExist(QuerySetError):
    """Base exception for all DoesNotExist exceptions.

    This is the parent class for all model-specific DoesNotExist exceptions.
    Use this to catch DoesNotExist from any model.

    Example:
        >>> try:
        ...     obj = await SomeModel.objects.get(id=1)
        ... except SomeModel.DoesNotExist:
        ...     # Catches DoesNotExist from any model
        ...     pass
    """

    pass


class MultipleObjectsReturned(QuerySetError):
    """Raised when .get() returns multiple objects instead of one.

    This exception occurs when using .get() to retrieve a single object but
    multiple rows match the filter criteria. It indicates the query needs
    more specific filters to uniquely identify one object.

    Raises:
        MultipleObjectsReturned: When .get() returns more than one result.

    Example:
        >>> # Wrong: multiple users with this name exist
        >>> user = await User.objects.get(name="John")
        MultipleObjectsReturned: get() returned multiple results

        >>> # Correct: use more specific filters
        >>> user = await User.objects.get(email="john@example.com")>>> # Or use filter() if you expect multiple results
        >>> users = await User.objects.filter(name="John")

    Note:
        Performance: Only raised at query execution time.
        Use .filter() when you expect multiple results.
        Use .get() only when filters guarantee uniqueness (e.g., by primary key).
    """

    pass
