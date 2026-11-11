"""QuerySet implementation for async SQLAlchemy ORM.

This module provides a fluent, chainable API for building and executing database
queries. The QuerySet acts as a facade coordinating query building, compilation,
execution, and write operations while maintaining immutability through cloning.

The design separates concerns:
- QueryBuilder: Constructs query state from method calls
- QueryCompiler: Translates state into SQLAlchemy expressions
- QueryExecutor: Executes queries and handles result processing
- QueryWriter: Handles create, update, delete operations
- QueryState: Immutable snapshot of query configuration
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from copy import copy
from typing import Any, Generic

from sqlalchemy.ext.asyncio import AsyncSession

from async_orm_kit._logging import get_logger
from async_orm_kit.config import ORMConfig
from async_orm_kit.exceptions import QueryStateError
from async_orm_kit.observers.queryset_observer import BaseQuerySetObserver
from async_orm_kit.query.compiler import QueryCompiler
from async_orm_kit.queryset.builder import QueryBuilder
from async_orm_kit.queryset.executor import QueryExecutor
from async_orm_kit.queryset.state import QueryState
from async_orm_kit.queryset.validators import QuerySetValidator
from async_orm_kit.queryset.writer import QueryWriter
from async_orm_kit.types import ModelType

logger = get_logger(__name__)


class QuerySet(Generic[ModelType]):
    """Lazy, chainable query interface for building and executing database queries.

    QuerySet provides a Django-inspired API for constructing database queries through
    method chaining. All query-building methods return new QuerySet instances, making
    the API immutable and allowing query reuse. Queries are only executed when results
    are accessed (lazy evaluation).

    The QuerySet coordinates four main components:
    - QueryBuilder: Translates method calls into query state
    - QueryCompiler: Converts state into SQLAlchemy expressions
    - QueryExecutor: Executes queries and processes results
    - QueryWriter: Handles data modification operations

    Args:
        model: The SQLAlchemy model class to query.
        session: Active async database session for query execution.
        observers: Optional sequence of observer instances for lifecycle hooks.
        config: Optional immutable query-planner configuration.

    Raises:
        ValueError: If model or session is None.

    Example:
        >>> # Basic filtering and ordering
        >>> users = await User.objects.filter(active=True).order_by("-created_at")
        >>> # Relationship traversal with eager loading
        >>> posts = await Post.objects.select_related("author").filter(
        ...     author__username="john"
        ... )

        >>> # Aggregation with filtering
        >>> stats = await Order.objects.values("customer_id").annotate(
        ...     total=Sum("amount")
        ... ).having(total__gte=1000)

        >>> # Async iteration for memory efficiency
        >>> async for user in User.objects.filter(active=True):
        ...     await process_user(user)

    Note:
        Performance: QuerySets are lazy - no database query occurs until you
        access results via await, iteration, or methods like count()/exists().
        Chain filters before eager loading (select_related/prefetch_related)
        for optimal query generation.
    """

    __slots__ = (
        "model",
        "session",
        "_state",
        "_compiler",
        "_observers",
        "_result_cache",
        "_sliced",
    )

    def __init__(
        self,
        model: type[ModelType],
        session: AsyncSession,
        observers: Sequence[BaseQuerySetObserver] | None = None,
        config: ORMConfig | None = None,
    ):
        if model is None:
            raise ValueError("Model cannot be None - Manager not properly bound")
        if session is None:
            raise ValueError("No database session was provided")

        self.model = model
        self.session = session
        self._state = QueryState(model=model)
        self._compiler = QueryCompiler(config=config)
        self._observers: list[BaseQuerySetObserver] = list(observers or [])
        self._result_cache: list[Any] | None = None
        self._sliced: bool = False

    def __repr__(self):
        """Return developer-friendly string representation of QuerySet state.

        Returns:
            String showing model name, filter count, ordering, and limit.

        Example:
            >>> qs = User.objects.filter(active=True).order_by("-id").limit(10)
            >>> print(qs)
            <QuerySet model=User filters=1 order_by=['-id'] limit=10
        """
        return (
            f"<QuerySet model={self._state.model.__name__} "
            f"filters={len(self._state.filters)} "
            f"order_by={self._state.order_by} "
            f"limit={self._state.limit_val}>"
        )

    def __await__(self):
        """Enable awaiting QuerySet directly to fetch all results.

        Returns:
            Awaitable that resolves to list of model instances or dicts.

        Example:
            >>> users = await User.objects.filter(active=True)
            >>> # Equivalent to:
            >>> users = await User.objects.filter(active=True)._fetch()

        Note:
            Performance: Executes database query and caches results.
            Subsequent awaits return cached data without re-querying.
        """
        return self._fetch().__await__()

    def stream(self) -> AsyncIterator[Any]:
        """Return an async iterator that streams results from the database.

        The underlying SQLAlchemy streaming result is closed when iteration
        completes, the iterator is explicitly closed, or the consuming task is
        cancelled. The caller owns the session and must keep it open while
        consuming the iterator.

        Returns:
            Async iterator yielding model instances, dictionaries, or tuples,
            depending on the QuerySet projection.

        Example:
            >>> async for user in User.objects.filter(session, active=True).stream():
            ...     await process_user(user)
        """
        return QueryExecutor(self).stream()

    def __aiter__(self) -> AsyncIterator[Any]:
        """Enable ``async for`` directly on a QuerySet."""
        return self.stream()

    def __getitem__(self, item: int | slice) -> QuerySet[ModelType]:
        """Support slicing and indexing via LIMIT/OFFSET.

        Always returns a new QuerySet — use await to execute.

        Args:
            item: Integer index or slice.

        Returns:
            QuerySet with appropriate LIMIT and OFFSET applied.

        Raises:
            TypeError: If step is provided or item is not int/slice.
            ValueError: If negative indexing is attempted.

        Example:
            >>> # LIMIT 10
            >>> books = await Book.objects.filter(published=True)[:10]
            >>>
            >>> # OFFSET 5 LIMIT 10
            >>> books = await Book.objects.filter(published=True)[5:15]
            >>>
            >>> # Single item: OFFSET 3 LIMIT 1
            >>> book = await Book.objects.order_by("title")[3]
            >>>
            >>> # OFFSET 5 (no limit)
            >>> books = await Book.objects.all()[5:]
        """
        if isinstance(item, slice):
            if item.step is not None:
                raise TypeError("QuerySet slicing does not support step")

            start = 0 if item.start is None else item.start
            stop = item.stop
            if type(start) is not int or (stop is not None and type(stop) is not int):
                raise TypeError("QuerySet slice bounds must be integers or None.")
            if start < 0 or (stop is not None and stop < 0):
                raise ValueError("Negative indexing is not supported")

            clone = self._clone()
            clone._state.offset_val = (self._state.offset_val or 0) + start
            remaining = self._state.limit_val
            if remaining is not None:
                remaining = max(remaining - start, 0)
            requested = max(stop - start, 0) if stop is not None else None
            clone._state.limit_val = (
                requested
                if remaining is None
                else remaining
                if requested is None
                else min(remaining, requested)
            )

            clone._sliced = True
            return clone

        elif isinstance(item, int) and not isinstance(item, bool):
            if item < 0:
                raise ValueError("Negative indexing is not supported")

            clone = self._clone()
            clone._state.offset_val = (self._state.offset_val or 0) + item
            clone._state.limit_val = (
                0
                if self._state.limit_val is not None and item >= self._state.limit_val
                else 1
            )
            clone._sliced = True

            return clone

        raise TypeError(
            f"QuerySet indices must be integers or slices, not {type(item).__name__}"
        )

    @property
    def state(self):
        """Access current query state for inspection or advanced usage.

        Returns:
            An isolated QueryState snapshot containing the current query configuration.

        Note:
            The returned state is detached from the QuerySet. Mutating the snapshot
            does not change the query being built.
        """
        return self._state.clone()

    @property
    def compiler(self):
        """Access query compiler for advanced SQL generation.

        Returns:
            QueryCompiler instance used to translate state into SQLAlchemy.

        Note:
            Rarely needed in application code - primarily for internal use
            and debugging query generation.
        """
        return self._compiler

    @property
    def observers(self):
        """Access registered lifecycle observers.

        Returns:
            Immutable tuple of observer instances attached to this QuerySet.

        Note:
            Observers receive callbacks during query execution lifecycle.
            Useful for logging, metrics, or custom query transformations.
        """
        return tuple(self._observers)

    def add_observer(self, observer: BaseQuerySetObserver) -> QuerySet[ModelType]:
        """Attach a lifecycle observer to receive query execution callbacks.

        Observers can hook into query building, execution, and result processing
        for logging, metrics collection, or custom transformations.

        Args:
            observer: Observer instance implementing BaseQuerySetObserver.

        Returns:
            A new QuerySet with the observer registered.

        Example:
            >>> class QueryLogger(BaseQuerySetObserver):
            ...     def before_execute(self, queryset):
            ...         logger.info(f"Executing: {queryset.sql()}")

            >>> qs = User.objects.add_observer(QueryLogger()).filter(active=True)

        Note:
            Performance: O(1) - observers are stored in a list.
            Observer registrations are copied when a QuerySet is cloned.
        """
        if not isinstance(observer, BaseQuerySetObserver):
            raise TypeError("observer must be a BaseQuerySetObserver instance.")
        clone = self._clone()
        clone._observers.append(observer)
        return clone

    def _clone(self) -> QuerySet[ModelType]:
        """Create a new QuerySet with copied state for immutable chaining.

        This is the core mechanism enabling immutable query building. Each
        query-modifying method clones the QuerySet before applying changes,
        ensuring the original remains unchanged.

        Returns:
            New QuerySet instance with deep-copied state but shared session.
        """
        if self._sliced:
            raise TypeError(
                "Cannot filter, reorder, or chain further querysets once a slice "
                "has been taken. Apply all filters and ordering before slicing."
            )

        new_qs = copy(self)
        new_qs.model = self.model
        new_qs.session = self.session
        new_qs._state = self._state.clone()
        new_qs._compiler = self._compiler
        new_qs._observers = self._observers.copy()
        new_qs._sliced = False  # <-- explicit: fresh clone is never sliced
        new_qs._result_cache = None
        return new_qs

    async def _fetch(self) -> list[ModelType]:
        """Execute query and return all results with caching.

        This is the core lazy evaluation method. Results are cached after first
        execution, so subsequent calls return cached data without re-querying.

        Returns:
            List of model instances, dicts, or tuples depending on query type.

        Raises:
            DatabaseError: If query execution fails.QueryStateError: If query state is invalid.

        Note:
            Performance: O(n) where n is result count. First call executes
            database query, subsequent calls return O(1) cached results.
            Cache is cleared when QuerySet is cloned.
        """
        if self._result_cache is not None:
            return self._result_cache

        executor = QueryExecutor(self)
        results = await executor.fetch_all()
        self._result_cache = results
        return results

    def filter(self, *args, **kwargs) -> QuerySet[ModelType]:
        """Add WHERE conditions to narrow query results.

        Filters are combined with AND logic. Multiple filter() calls accumulate
        conditions. Supports field lookups (exact, gt, contains, etc.) and
        relationship traversal using double underscores.

        Args:
            *args: Q objects for complex boolean logic.
            **kwargs: Field lookups in format field__lookup=value.

        Returns:
            New QuerySet with additional filters applied.

        Raises:
            ModelAttributeError: If field doesn't exist on model.
            InvalidLookupError: If lookup operator is invalid for field type.
            RelationshipError: If relationship path is invalid.

        Example:
            >>> # Simple equality
            >>> User.objects.filter(active=True, role="admin")

            >>> # Field lookups
            >>> User.objects.filter(age__gte=18, username__icontains="john")

            >>> # Relationship traversal
            >>> Post.objects.filter(author__username="john", author__active=True)

            >>> # Complex logic with Q objects
            >>> from async_orm_kit.query.q import Q
            >>> User.objects.filter(Q(role="admin") | Q(role="moderator"))

            >>> # Chaining filters (AND logic)
            >>> User.objects.filter(active=True).filter(age__gte=18)

        Note:
            Performance: O(1) for state modification, no database query.
            Filters are applied in the order specified, which can affect
            query plan optimization. Place most selective filters first.
        """
        if not args and not kwargs:
            return self
        clone = self._clone()
        builder = QueryBuilder(clone._state)
        clone._state = builder.filter(*args, **kwargs)
        return clone

    def exclude(self, *args, **kwargs) -> QuerySet[ModelType]:
        """Exclude rows whose combined predicate evaluates to TRUE.

        Opposite of filter() - excludes rows matching the criteria. Nullable
        predicates that evaluate to SQL UNKNOWN are retained. Multiple exclude()
        calls accumulate with AND logic (exclude A AND exclude B).

        Args:
            *args: Q objects for complex boolean exclusions.
            **kwargs: Field lookups in format field__lookup=value.

        Returns:
            New QuerySet with exclusion filters applied.

        Raises:
            ModelAttributeError: If field doesn't exist on model.
            InvalidLookupError: If lookup operator is invalid.
        Example:
            >>> # Exclude inactive users
            >>> User.objects.exclude(active=False)

            >>> # Exclude multiple conditions
            >>> User.objects.exclude(role="guest", verified=False)

            >>> # Combine with filter
            >>> User.objects.filter(age__gte=18).exclude(banned=True)

        Note:
            Performance: O(1) for state modification. A single call combines
            keyword predicates with AND before excluding rows where the result is
            TRUE. SQL UNKNOWN values are retained, including missing nullable
            relationships. Separate exclude() calls accumulate with AND logic.
        """
        if not args and not kwargs:
            return self
        clone = self._clone()
        builder = QueryBuilder(clone._state)
        clone._state = builder.exclude(*args, **kwargs)
        return clone

    def order_by(self, *fields: str) -> QuerySet[ModelType]:
        """Specify result ordering using field names.

        Controls the ORDER BY clause. Prefix field names with '-' for descending
        order. Multiple fields create multi-level sorting. Replaces any previous
        ordering when called.

        Args:
            *fields: Field names, optionally prefixed with '-' for descending.

        Returns:
            New QuerySet with specified ordering.

        Raises:
            ModelAttributeError: If field doesn't exist.
            QueryStateError: If ordering field not in values() selection.
            ExpressionTypeError: If F() expression lacks .asc()/.desc().

        Example:
            >>> # Single field ascending
            >>> User.objects.order_by("username")

            >>> # Descending order
            >>> User.objects.order_by("-created_at")

            >>> # Multi-level sorting
            >>> User.objects.order_by("role", "-created_at")

            >>> # Relationship fields
            >>> Post.objects.order_by("author__username")
            >>> # Expression ordering
            >>> from async_orm_kit.query.expressions import F
            >>> Product.objects.order_by((F("price") * F("quantity")).desc())

        Note:
            Performance: O(1) for state modification. Database sorting is
            O(n log n). Add indexes on frequently ordered fields.
            Calling order_by() replaces previous ordering - it doesn't append.
        """
        if not fields:
            return self

        validator = QuerySetValidator(self._state)
        validator.validate_order_by_fields(fields)
        clone = self._clone()
        builder = QueryBuilder(clone._state)
        clone._state = builder.order_by(*fields)
        return clone

    def limit(self, limit: int) -> QuerySet[ModelType]:
        """Restrict maximum number of results returned.

        Adds LIMIT clause to query. Useful for pagination or preventing
        excessive memory usage with large result sets.

        Args:
            limit: Maximum number of rows to return.

        Returns:
            New QuerySet with limit applied.

        Example:
            >>> # Get first 10 users
            >>> User.objects.order_by("-created_at").limit(10)

            >>> # Pagination with offset
            >>> User.objects.order_by("id").limit(20).offset(40)  # Page 3

        Note:
            Performance: O(1) for state modification. Database applies limit
            efficiently, stopping after n rows. Always use with order_by()
            for consistent results.
        """
        clone = self._clone()
        builder = QueryBuilder(clone._state)
        clone._state = builder.limit(limit)
        return clone

    def offset(self, offset: int) -> QuerySet[ModelType]:
        """Skip specified number of results before returning rows.

        Adds OFFSET clause for pagination. Should be used with limit() and
        order_by() for consistent pagination.

        Args:
            offset: Number of rows to skip.

        Returns:
            New QuerySet with offset applied.

        Example:
            >>> # Skip first 20 results
            >>> User.objects.order_by("id").offset(20).limit(10)

            >>> # Page-based pagination
            >>> page, page_size = 3, 20
            >>> User.objects.order_by("id").offset((page - 1) * page_size).limit(page_size)

        Note:
            Performance: O(1) for state modification. Database must scan
            offset rows before returning results - large offsets are slow.
            Consider cursor-based pagination for better performance:
            User.objects.filter(id__gt=last_id).limit(20)
        """
        clone = self._clone()
        builder = QueryBuilder(clone._state)
        clone._state = builder.offset(offset)
        return clone

    def union(self, *querysets: QuerySet, all: bool = False):
        """UNION or UNION ALL matching QuerySets in the same session.

        Branches must have the same model and projection; apply ordering and
        pagination to the combined result, not individual branches.
        """
        from async_orm_kit.queryset.compound import CompoundQuerySet

        if not querysets:
            raise ValueError("union() requires at least one other QuerySet")
        if not isinstance(all, bool):
            raise TypeError("all must be a boolean")
        return CompoundQuerySet((self, *querysets), "union_all" if all else "union")

    def intersection(self, *querysets: QuerySet):
        """INTERSECT matching QuerySets in the same session."""
        from async_orm_kit.queryset.compound import CompoundQuerySet

        if not querysets:
            raise ValueError("intersection() requires at least one other QuerySet")
        return CompoundQuerySet((self, *querysets), "intersect")

    def difference(self, *querysets: QuerySet):
        """EXCEPT matching QuerySets in the same session."""
        from async_orm_kit.queryset.compound import CompoundQuerySet

        if not querysets:
            raise ValueError("difference() requires at least one other QuerySet")
        return CompoundQuerySet((self, *querysets), "except")

    def cte(self, name: str | None = None, *, recursive: bool = False):
        """Compile this QuerySet as a SQLAlchemy common table expression.

        The returned CTE exposes ``.c`` columns and SQLAlchemy's ``union_all``
        method for recursive terms. Consume it with a SQLAlchemy ``select`` or
        a mapped SQL expression; the current QuerySet is not modified.
        """
        if name is not None and (not isinstance(name, str) or not name.isidentifier()):
            raise ValueError("CTE name must be a valid SQL identifier")
        if not isinstance(recursive, bool):
            raise TypeError("recursive must be a boolean")
        if self._state.for_update is not None:
            from async_orm_kit.exceptions import QueryStateError

            raise QueryStateError("CTEs cannot use select_for_update()")
        if self._state.select_related or self._state.prefetch_related:
            from async_orm_kit.exceptions import QueryStateError

            raise QueryStateError("CTEs cannot include ORM eager-loading directives")
        statement = self._compiler.compile(self.state)
        return statement.cte(name=name, recursive=recursive)

    def select_related(self, *relations: str) -> QuerySet[ModelType]:
        """Eagerly load related objects using SQL JOINs.

        Performs JOIN operations to fetch related objects in a single query,
        avoiding N+1 query problems. Best for foreign key (many-to-one) and
        one-to-one relationships. Use double underscores for nested relations.

        Args:
            *relations: Relationship field names to join and load.

        Returns:
            New QuerySet with eager loading configured.

        Raises:
            RelationshipError: If relationship doesn't exist or isn't joinable.
            QueryStateError: If called after values() or values_list().

        Example:
            >>> # Load user's profile in same query
            >>> User.objects.select_related("profile")

            >>> # Multiple relationships
            >>> Post.objects.select_related("author", "category")

            >>> # Nested relationships
            >>> Comment.objects.select_related("post__author__profile")

            >>> # Avoid N+1 queries
            >>> # Bad: N+1 queries
            >>> for post in await Post.objects.all():
            ...     print(post.author.username)  # Separate query each time

            >>> # Good: Single query with JOIN
            >>> for post in await Post.objects.select_related("author"):
            ...     print(post.author.username)  # Already loaded

        Note:
            Performance: Reduces query count from N+1 to 1, but increases
            single query complexity and result size. Use for relationships
            accessed frequently. Avoid for one-to-many (use prefetch_related).
            JOINs can be expensive - profile queries with many relations.
        """
        validator = QuerySetValidator(self._state)
        validator.validate_requires_model_instances("select_related")

        clone = self._clone()
        builder = QueryBuilder(clone._state)
        clone._state = builder.select_related(*relations)
        return clone

    def select_for_update(
        self,
        *,
        nowait: bool = False,
        skip_locked: bool = False,
        read: bool = False,
        key_share: bool = False,
        of: Any = None,
    ) -> QuerySet[ModelType]:
        """Lock selected rows until the caller commits/rolls back.

        This is supported for explicitly transactional PostgreSQL/MySQL queries.
        SQLite cannot enforce row locks and is rejected at execution time.
        ``nowait`` and ``skip_locked`` are mutually exclusive; support for each
        flag is database/version dependent.
        """
        if any(not isinstance(v, bool) for v in (nowait, skip_locked, read, key_share)):
            raise TypeError("Row-lock flags must be booleans")
        if nowait and skip_locked:
            raise ValueError("nowait and skip_locked are mutually exclusive")
        if (
            self._state.values_select is not None
            or self._state.values_list_select is not None
        ):
            raise ValueError("select_for_update requires model instances")
        clone = self._clone()
        clone._state.for_update = {
            "nowait": nowait,
            "skip_locked": skip_locked,
            "read": read,
            "key_share": key_share,
        }
        if of is not None:
            clone._state.for_update["of"] = of
        return clone

    def prefetch_related(self, *relations: str) -> QuerySet[ModelType]:
        """Eagerly load related objects using separate queries.

        Performs additional queries to fetch related objects, then joins them
        in Python. Best for reverse foreign keys (one-to-many) and many-to-many
        relationships where JOINs would create duplicate rows.

        Args:
            *relations: Relationship field names to prefetch.

        Returns:
            New QuerySet with prefetch configured.

        Raises:
            RelationshipError: If relationship doesn't exist.
            QueryStateError: If called after values() or values_list().

        Example:
            >>> # Load all posts for each author
            >>> Author.objects.prefetch_related("posts")

            >>> # Many-to-many relationships
            >>> Post.objects.prefetch_related("tags")

            >>> # Nested prefetch
            >>> Author.objects.prefetch_related("posts__comments")

            >>> # Combine with select_related
            >>> Post.objects.select_related("author").prefetch_related("comments")

        Note:
            Performance: Executes 1 + N queries where N is number of prefetch
            relations. More queries than select_related but avoids JOIN
            multiplication. Efficient for one-to-many where JOINs would
            duplicate parent rows. Memory usage increases with result size.
        """
        validator = QuerySetValidator(self._state)
        validator.validate_requires_model_instances("prefetch_related")

        clone = self._clone()
        builder = QueryBuilder(clone._state)
        clone._state = builder.prefetch_related(*relations)
        return clone

    def annotate(self, **annotations) -> QuerySet[ModelType]:
        """Add computed fields using aggregate functions or expressions.

        Adds calculated columns to query results using SQL expressions. Supports
        aggregate functions (Count, Sum, Avg, etc.) and field expressions (F).
        Annotated fields are accessible as attributes on result objects.

        Args:
            **annotations: Name=expression pairs for computed fields.

        Returns:
            New QuerySet with annotations added.

        Example:
            >>> from async_orm_kit.query.aggregations import Count, Sum, Avg
            >>> from async_orm_kit.query.expressions import F

            >>> # Count related objects
            >>> Author.objects.annotate(post_count=Count("posts"))

            >>> # Sum numeric fields
            >>> Order.objects.annotate(total=Sum("items__price"))

            >>> # Computed expressions
            >>> Product.objects.annotate(
            ...     revenue=F("price") * F("quantity"),
            ...     discounted=F("price") * 0.9
            ... )

            >>> # Multiple aggregates
            >>> Customer.objects.annotate(
            ...     order_count=Count("orders"),
            ...     total_spent=Sum("orders__amount"),
            ...     avg_order=Avg("orders__amount")
            ... )

        Note:
            Performance: Aggregates are computed by database, not Python.
            Use with values() for grouping. Annotations add to SELECT clause,
            increasing query complexity. Heavy aggregates may need indexes.
        """
        clone = self._clone()
        builder = QueryBuilder(clone._state)
        clone._state = builder.annotate(**annotations)
        return clone

    def having(self, *conditions, **lookups) -> QuerySet[ModelType]:
        """Filter results based on aggregate annotations.

        Applies WHERE-like filtering to aggregated results. Must be used after
        annotate() with at least one aggregate function. Multiple having() calls
        accumulate with AND logic, similar to filter().

        Args:
            **lookups: Field lookups on annotated aggregate fields.

        Returns:
            New QuerySet with HAVING clause applied.

        Raises:
            QueryStateError: If called without prior aggregate annotations.
            FilterError: If lookup references non-existent annotation.
            InvalidLookupError: If lookup is invalid.

        Example:
            >>> from async_orm_kit.query.aggregations import Count, Sum
            >>> # Filter by aggregate count
            >>> Author.objects.annotate(
            ...     post_count=Count("posts")
            ... ).having(post_count__gte=5)

            >>> # Multiple conditions
            >>> Customer.objects.annotate(
            ...     order_count=Count("orders"),
            ...     total_spent=Sum("orders__amount")
            ... ).having(order_count__gt=10, total_spent__gte=1000)

            >>> # Chained having (AND logic)
            >>> Order.objects.values("customer_id").annotate(
            ...     cnt=Count("id"),
            ...     revenue=Sum("amount")
            ... ).having(cnt__gt=5).having(revenue__gte=1000)

            >>> # Implicit exact lookup
            >>> Order.objects.annotate(
            ...     status_count=Count("id")
            ... ).having(status_count=10)

        Note:
            Performance: HAVING filters after aggregation, so it doesn't reduce
            rows scanned. Use filter() before annotate() to reduce aggregation
            work. HAVING is applied after GROUP BY in SQL execution order.
        """
        if not conditions and not lookups:
            return self
        validator = QuerySetValidator(self._state)
        validator.validate_having_requires_aggregates()

        clone = self._clone()
        builder = QueryBuilder(clone._state)
        clone._state = builder.having(*conditions, **lookups)
        return clone

    def all(self) -> QuerySet[ModelType]:
        """Return a clone of the current QuerySet.

        Creates a fresh QuerySet with identical state. Useful for creating
        independent query variations from a base QuerySet.

        Returns:
            New QuerySet with same configuration.

        Example:
            >>> # Create base query
            >>> users = User.objects.all()

            >>> # Create variations without modifying base
            >>> active_users = users.filter(is_active=True)
            >>> recent = users.order_by("-created_at").limit(10)

            >>> # Base query unchanged
            >>> all_users = await users
        Note:
            Performance: O(1) cloning operation. Result cache is not copied.
        """
        return self._clone()

    async def first(self) -> ModelType | None:
        """Fetch the first result or None if no results exist.

        Automatically applies limit(1) and returns single object. Returns None
        instead of raising exception when no results found. Respects existing
        ordering or uses default model ordering.

        Returns:
            First matching object or None.

        Raises:
            DatabaseError: If query execution fails.

        Example:
            >>> # Get most recent user
            >>> user = await User.objects.order_by("-created_at").first()
            >>> if user:
            ...     print(user.username)

            >>> # With filtering
            >>> admin = await User.objects.filter(role="admin").first()

            >>> # Returns None when no match
            >>> guest = await User.objects.filter(role="guest").first()
            >>> assert guest is None

        Note:
            Performance: Executes database query with LIMIT 1. O(1) result
            processing. More efficient than fetching all results and taking
            first element. Does not use result cache.
        """
        logger.debug(f"Executing .first() on {self.model.__name__}")
        clone = self._clone()
        clone._state.limit_val = (
            min(clone._state.limit_val, 1) if clone._state.limit_val is not None else 1
        )
        executor = QueryExecutor(clone)
        return await executor.first()

    async def get(self, **kwargs) -> ModelType:
        """Fetch single object matching criteria, raising exception if not found.

        Returns exactly one object. Raises exception if zero or multiple objects
        match. Use when you expect exactly one result and want to fail loudly
        if that assumption is violated.

        Args:
            **kwargs: Optional filter criteria. If omitted, uses existing filters.

        Returns:
            Single matching model instance.

        Raises:
            Model.DoesNotExist: If no objects match the query.
            Model.MultipleObjectsReturned: If multiple objects match.
            ModelAttributeError: If filter field doesn't exist.

        Example:
            >>> # Get by primary key
            >>> user = await User.objects.get(id=123)

            >>> # Get by unique field
            >>> user = await User.objects.get(email="john@example.com")

            >>> # With existing filters
            >>> admin = await User.objects.filter(role="admin").get(username="john")

            >>> # Raises if not found
            >>> try:
            ...     user = await User.objects.get(id=999)
            ... except User.DoesNotExist:
            ...     print("User not found")

            >>> # Raises if multiple match
            >>> try:
            ...     user = await User.objects.get(role="admin")  # Multiple admins
            ... except User.MultipleObjectsReturned:
            ...     print("Multiple users found")
            >>> # Catch any model's DoesNotExist
            >>> try:
            ...     obj = await SomeModel.objects.get(id=1)
            ... except ObjectDoesNotExist:
            ...     print("Object not found")

        Note:
            Performance: Executes database query without LIMIT. O(n) if multiple
            results exist (to detect error condition). Use first() if you only
            want one result and don't care about duplicates.
        """
        if kwargs:
            # Clone because we're modifying state with filters
            clone = self._clone()
            builder = QueryBuilder(clone._state)
            clone._state = builder.filter(**kwargs)
            executor = QueryExecutor(clone)
        else:
            # No state modification, use current instance
            executor = QueryExecutor(self)

        return await executor.get()

    async def aggregate(self, **aggregates: Any) -> dict[str, Any]:
        """Compute named aggregate results over the filtered model rows.

        Unlike ``annotate()``, this returns one dictionary instead of model
        instances. Grouped/projected, sliced and previously annotated queries
        are rejected: flattening those shapes into a single group is ambiguous.
        For grouped results, use ``values(...).annotate(...)`` instead.
        """
        import keyword

        from sqlalchemy import select

        from async_orm_kit.exceptions import DatabaseError, QueryStateError
        from async_orm_kit.query.aggregations.base import Aggregate
        from async_orm_kit.query.lookups.resolver import ResolverContext

        if self._state.for_update is not None:
            raise QueryStateError(
                "aggregate() cannot be combined with select_for_update()"
            )
        if not aggregates:
            raise ValueError("aggregate() requires at least one named aggregate")
        state = self._state.clone()
        if (
            state.values_select is not None
            or state.values_list_select is not None
            or state.annotations
            or state.has_aggregates
            or state.having_conditions
            or state.limit_val is not None
            or state.offset_val is not None
            or state.distinct_on
            or state.is_distinct
        ):
            raise QueryStateError(
                "aggregate() requires an unsliced, ungrouped model QuerySet"
            )
        expressions = []
        for alias, aggregate in aggregates.items():
            if (
                not alias.isidentifier()
                or keyword.iskeyword(alias)
                or alias.startswith("_")
            ):
                raise ValueError(f"Invalid aggregate alias: {alias!r}")
            if not isinstance(aggregate, Aggregate):
                raise TypeError(f"aggregate({alias}=...) requires an Aggregate")
            from async_orm_kit.queryset.builder import QueryBuilder

            expression, _ = QueryBuilder(state)._resolve_expression(
                aggregate, ResolverContext(in_annotation=True)
            )
            expressions.append(expression.label(alias))
        stmt = select(*expressions).select_from(self.model)
        stmt = self._compiler._apply_joins(stmt, state)
        stmt = self._compiler._apply_filters(stmt, state)
        try:
            row = (await self.session.execute(stmt)).one()
        except Exception as exc:
            raise DatabaseError("Error executing aggregate()", cause=exc) from exc
        return dict(zip(aggregates, row, strict=False))

    async def count(self) -> int:
        """Return the number of matching rows without fetching data.

        Executes COUNT(*) query for efficiency. Does not load actual objects
        into memory. Respects all filters but ignores limit/offset.

        Returns:
            Total number of matching rows.

        Raises:
            DatabaseError: If query execution fails.

        Example:
            >>> # Count all users
            >>> total = await User.objects.count()
            >>> # Count with filters
            >>> active_count = await User.objects.filter(active=True).count()

            >>> # Count ignores limit
            >>> qs = User.objects.limit(10)
            >>> count = await qs.count()  # Returns total, not 10

        Note:
            Performance: O(n) database scan but no data transfer or object
            creation. Much faster than len(await queryset). Use indexes on
            filtered fields for fast counts. Does not use result cache.
        """
        executor = QueryExecutor(self)
        return await executor.count()

    async def exists(self) -> bool:
        """Check if any matching rows exist without fetching data.

        Executes optimized EXISTS query using SELECT 1 with LIMIT 1 that stops
        after finding first match. More efficient than count() > 0 when you only
        need boolean result.

        Returns:
            True if at least one matching row exists, False otherwise.

        Raises:
            DatabaseError: If query execution fails.

        Example:
            >>> # Check if any active users exist
            >>> has_active = await User.objects.filter(active=True).exists()

            >>> # Conditional logic
            >>> if await User.objects.filter(email=email).exists():
            ...     raise ValueError("Email already registered")

            >>> # More efficient than count
            >>> # Bad: await User.objects.filter(active=True).count() > 0
            >>> # Good: await User.objects.filter(active=True).exists()

        Note:
            Performance: O(1) in best case - stops after first match using LIMIT 1.
            Much faster than count() for large result sets. Does not use result cache.
        """
        executor = QueryExecutor(self)
        return await executor.exists()

    def values(self, *fields: str) -> QuerySet:
        """Return dictionaries instead of model instances.

        Projects specific fields into dict results. Useful for reducing memory
        usage and network transfer when you don't need full model instances.
        Supports relationship traversal with double underscores.

        Args:
            *fields: Field names to include in result dicts.

        Returns:
            New QuerySet returning list of dicts instead of model instances.

        Raises:
            QueryStateError: If called after only() or defer().
            ModelAttributeError: If field doesn't exist.

        Example:
            >>> # Select specific fields
            >>> users = await User.objects.values("id", "username")
            >>> # [{"id": 1, "username": "john"}, {"id": 2, "username": "jane"}]

            >>> # Relationship fields
            >>> posts = await Post.objects.values("title", "author__username")
            >>> # [{"title": "Hello", "author__username": "john"}]

            >>> # With aggregation for grouping
            >>> stats = await Order.objects.values("customer_id").annotate(
            ...     total=Sum("amount")
            ... )
            >>> # [{"customer_id": 1, "total": 1500}, ...]

        Note:
            Performance: Reduces memory usage by not creating model instances.
        """
        validator = QuerySetValidator(self._state)
        validator.validate_not_deferred_loading("values")
        clone = self._clone()
        clone._state = QueryBuilder(clone._state).values(*fields)
        return clone

    def values_list(self, *fields: str, flat: bool = False) -> QuerySet:
        """Return tuples instead of model instances.

        Projects fields into tuple results. More memory-efficient than values()
        when you don't need field names. Use flat=True with single field to get
        flat list instead of single-element tuples.

        Args:
            *fields: Field names to include in result tuples.
            flat: If True and single field, return flat list instead of tuples.

        Returns:
            New QuerySet returning list of tuples or flat list.

        Raises:
            QueryStateError: If called after only() or defer().
            ValueError: If flat=True with multiple fields.
            ModelAttributeError: If field doesn't exist.

        Example:
            >>> # Multiple fields as tuples
            >>> users = await User.objects.values_list("id", "username")
            >>> # [(1, "john"), (2, "jane")]

            >>> # Single field as flat list
            >>> usernames = await User.objects.values_list("username", flat=True)
            >>> # ["john", "jane", "alice"]

            >>> # Relationship fields
            >>> titles = await Post.objects.values_list("title", "author__username")
            >>> # [("Hello World", "john"), ("Python Tips", "jane")]

            >>> # Error: flat with multiple fields
            >>> await User.objects.values_list("id", "username", flat=True)
            ValueError: flat=True requires exactly one field

        Note:
            Performance: Most memory-efficient projection method. Tuples use
            less memory than dicts. Use flat=True for single-field queries
            to avoid tuple overhead. Does not create model instances.
        """
        validator = QuerySetValidator(self._state)
        validator.validate_not_deferred_loading("values_list")
        validator.validate_values_list_flat(fields, flat)

        clone = self._clone()
        clone._state = QueryBuilder(clone._state).values_list(*fields, flat=flat)
        return clone

    def distinct(self, *fields: str) -> QuerySet:
        """Remove duplicate rows from query results.

        Applies SQL DISTINCT to eliminate duplicate rows. Without arguments,
        removes duplicates across all selected fields. With arguments (PostgreSQL
        only), applies DISTINCT ON to remove duplicates based on specific fields.

        Args:
            *fields: Optional field names for DISTINCT ON (PostgreSQL only).

        Returns:
            New QuerySet with DISTINCT applied.

        Raises:
            QueryStateError: If DISTINCT ON used with incompatible operations.
            ModelAttributeError: If field doesn't exist.

        Example:
            >>> # Remove all duplicate rows
            >>> User.objects.values("role").distinct()
            >>> # [{"role": "admin"}, {"role": "user"}]

            >>> # PostgreSQL DISTINCT ON - first occurrence per group
            >>> Order.objects.order_by("customer_id", "-created_at").distinct("customer_id")
            >>> # Returns most recent order per customer

            >>> # With relationship fields
            >>> Post.objects.distinct("author__username")

            >>> # Combine with filters
            >>> User.objects.filter(active=True).values("role").distinct()

        Note:
            Performance: DISTINCT requires sorting/hashing all rows - O(n log n).
            Can be expensive on large result sets. Consider filtering instead
            if possible. DISTINCT ON (PostgreSQL) requires ORDER BY to include
            distinct fields first. Add indexes on distinct fields.
        """
        validator = QuerySetValidator(self._state)
        validator.validate_distinct_fields(fields)

        clone = self._clone()
        clone._state = QueryBuilder(clone._state).distinct(*fields)
        return clone

    def with_lookup(self, name: str, handler: Any) -> QuerySet[ModelType]:
        """Register a SQL predicate builder on this QuerySet only.

        ``handler(column, value)`` must return a SQLAlchemy Boolean SQL
        expression. Built-in and delegated lookup names cannot be overwritten.
        Registration is immutable and safe for concurrently constructed queries.
        """
        import re

        from async_orm_kit.query.lookups import LOOKUP_OPERATORS, STANDARD_LOOKUPS

        if (
            not isinstance(name, str)
            or "__" in name
            or re.fullmatch(r"[a-z][a-z0-9_]*", name) is None
        ):
            raise ValueError("Custom lookup names must match [a-z][a-z0-9_]*")
        if name in LOOKUP_OPERATORS or name in STANDARD_LOOKUPS:
            raise ValueError(f"Cannot override built-in lookup {name!r}")
        if not callable(handler):
            raise TypeError("Custom lookup handler must be callable")
        clone = self._clone()
        clone._state.custom_lookups[name] = handler
        return clone

    def with_lookups(self, **handlers: Any) -> QuerySet[ModelType]:
        """Register several custom lookups on an isolated QuerySet clone."""
        query = self
        for name, handler in handlers.items():
            query = query.with_lookup(name, handler)
        return query

    def defer(self, *fields: str) -> QuerySet[ModelType]:
        """Exclude specified scalar columns from the initial model SELECT.

        In async code, fetch a deferred field explicitly with
        ``await session.refresh(instance, ["field_name"])`` rather than relying
        on implicit lazy loading, which can raise ``MissingGreenlet``.
        """
        QuerySetValidator(self._state).validate_requires_model_instances("defer")
        if self._state.only_fields is not None:
            raise QueryStateError("defer() cannot be combined with only()")
        clone = self._clone()
        clone._state = QueryBuilder(clone._state).defer(*fields)
        return clone

    def _seek(self, values: tuple[Any, ...], *, before: bool) -> QuerySet[ModelType]:
        """Apply a strict lexicographic cursor predicate to the current ordering."""
        from sqlalchemy import and_, inspect as sa_inspect, or_

        state = self._state
        fields = state.ordering_fields
        if not fields or len(fields) != len(values):
            raise QueryStateError(
                "seek_after()/seek_before() require one cursor value per "
                "order_by() field"
            )
        if state.seek_active:
            raise QueryStateError("Apply only one seek cursor per QuerySet")
        if state.offset_val is not None or state.is_distinct or state.distinct_on:
            raise QueryStateError("Keyset pagination cannot use OFFSET or DISTINCT")
        if state.has_aggregates or state.group_by_fields or state.having_conditions:
            raise QueryStateError("Keyset pagination does not support grouped queries")
        if state.for_update is not None:
            raise QueryStateError("Keyset pagination cannot use row locking")
        if any(value is None for value in values):
            raise ValueError("Keyset pagination does not support NULL cursor values")
        # A cursor is literal pagination data, not another SQL expression.
        # Reject iterable/SQL-building objects instead of relying on accidental
        # coercion by a database dialect.
        from enum import Enum
        from uuid import UUID

        from async_orm_kit.types import SAFE_SCALAR_TYPES

        if any(
            not isinstance(value, SAFE_SCALAR_TYPES + (UUID, Enum, bytes))
            for value in values
        ):
            raise TypeError("Keyset cursor values must be scalar Python values")
        for node in state.join_graph.join_order:
            if (
                node.relationship is not None
                and node.relationship.uselist
                and (node.used_in_filter or node.used_in_order_by)
            ):
                raise QueryStateError(
                    "Keyset pagination cannot follow collection JOIN filters or "
                    "ordering: SQL row multiplication makes cursors unstable"
                )

        inspected = sa_inspect(self.model)
        mapper = inspected.mapper if inspected.is_aliased_class else inspected
        columns = []
        directions = []
        field_names = []
        for field in fields:
            descending = field.startswith("-")
            name = field[1:] if descending else field
            if not name.isidentifier() or name not in mapper.column_attrs:
                raise QueryStateError(
                    "Keyset ordering requires root-model mapped scalar columns"
                )
            attribute = mapper.column_attrs[name]
            if len(attribute.columns) != 1:
                raise QueryStateError("Keyset ordering requires simple mapped columns")
            if attribute.columns[0].nullable:
                raise QueryStateError(
                    "Keyset ordering requires non-nullable mapped columns"
                )
            columns.append(getattr(self.model, name))
            directions.append(descending)
            field_names.append(name)
        pk_names = {col.key for col in mapper.primary_key}
        if not pk_names.issubset(field_names):
            raise QueryStateError(
                "Keyset ordering must include all primary-key columns as a "
                "deterministic tie-breaker"
            )
        if len(set(field_names)) != len(field_names):
            raise QueryStateError("Keyset ordering contains duplicate fields")

        predicates = []
        for index, (column, value, descending) in enumerate(
            zip(columns, values, directions, strict=False)
        ):
            go_lower = descending != before
            comparison = column < value if go_lower else column > value
            prefix = [columns[j] == values[j] for j in range(index)]
            predicates.append(and_(*prefix, comparison))

        clone = self._clone()
        clone._state.filters.append(or_(*predicates))
        clone._state.seek_active = True
        return clone

    def seek_after(self, *cursor_values: Any) -> QuerySet[ModelType]:
        """Return rows strictly after the supplied ordered cursor values."""
        return self._seek(cursor_values, before=False)

    def seek_before(self, *cursor_values: Any) -> QuerySet[ModelType]:
        """Return rows strictly before the supplied ordered cursor values.

        This preserves the current ORDER BY direction; it does not automatically
        reverse result order for backwards-page presentation.
        """
        return self._seek(cursor_values, before=True)

    def only(self, *fields: str) -> QuerySet:
        """Load only specified fields, deferring others (deferred loading).

        Optimizes queries by loading only required fields. Deferred fields are
        loaded on access with additional queries. Useful for models with large
        text/binary fields you don't always need. Returns model instances, not dicts.

        Args:
            *fields: Field names to load immediately. Others are deferred.

        Returns:
            New QuerySet with deferred loading configured.

        Raises:
            QueryStateError: If called after values() or values_list().
            ModelAttributeError: If field doesn't exist.

        Example:
            >>> # Load only id and username, defer other fields
            >>> users = await User.objects.only("id", "username")
            >>> print(users[0].username)  # Loaded
            >>> print(users[0].bio)  # Triggers additional query

            >>> # Relationship fields
            >>> posts = await Post.objects.only("title", "author__username")

            >>> # Useful for large fields
            >>> articles = await Article.objects.only("id", "title")
            >>> # content field (large text) not loaded unless accessed

            >>> # Combine with select_related
            >>> posts = await Post.objects.select_related("author").only(
            ...     "title", "author__username"
            ... )

        Note:
            Performance: Reduces initial query size and memory usage. However,
            accessing deferred fields triggers N additional queries. Use when
            you're certain fields won't be accessed. For read-only projections,
            use values() instead. Primary key is always loaded.
        """
        validator = QuerySetValidator(self._state)
        validator.validate_requires_model_instances("only")
        if self._state.deferred_fields:
            raise QueryStateError("only() cannot be combined with defer()")

        clone = self._clone()
        clone._state = QueryBuilder(clone._state).only(*fields)
        return clone

    async def create(self, **kwargs) -> ModelType:
        """Create and save a new model instance.

        Inserts a single row into the database and returns the created instance
        with database-generated fields (id, timestamps) populated.

        Args:
            **kwargs: Field values for the new instance.

        Returns:
            Newly created model instance with all fields populated.

        Raises:
            DatabaseError: If insert fails (constraint violation, etc.).

        Example:
            >>> # Create new user
            >>> user = await User.objects.create(
            ...     username="john",
            ...     email="john@example.com",
            ...     active=True
            ... )
            >>> print(user.id)  # Auto-generated ID
            >>> # With relationships
            >>> post = await Post.objects.create(
            ...     title="Hello World",
            ...     author=user,
            ...     content="First post"
            ... )

            >>> # Handle conflicts
            >>> try:
            ...     await User.objects.create(email="duplicate@example.com")
            ... except DatabaseError as e:
            ...     print("Email already exists")

        Note:
            Performance: Single INSERT query with RETURNING clause. O(1) operation.
            For multiple inserts, use bulk_create() instead - much faster.
            Triggers model validation and database constraints.
        """
        writer = QueryWriter(self)
        return await writer.create(**kwargs)

    async def bulk_create(
        self,
        objects: list[dict],
        *,
        returning: bool = False,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: list[str] | None = None,
        unique_fields: list[str] | None = None,
    ) -> list | int:
        """Create multiple instances in a single database operation.

        Efficiently inserts multiple rows using a single INSERT statement. Much
        faster than calling create() in a loop. Supports conflict handling for
        upsert operations (INSERT ... ON CONFLICT).

        Args:
            objects: List of dicts containing field values for each instance.
            returning: If True, return created instances. If False, return count.
            ignore_conflicts: Skip rows that violate unique constraints.
            update_conflicts: Update existing rows on conflict (upsert).
            update_fields: Fields to update on conflict (required if update_conflicts=True).
            unique_fields: Fields to check for conflicts (required if update_conflicts=True).

        Returns:
            List of created instances if returning=True, otherwise count of rows inserted.

        Raises:
            DatabaseError: If insert fails and ignore_conflicts=False.
            ValueError: If update_conflicts=True without update_fields/unique_fields.

        Example:
            >>> # Basic bulk insert - returns count
            >>> count = await User.objects.bulk_create([
            ...     {"username": "alice", "email": "alice@example.com"},
            ...     {"username": "bob", "email": "bob@example.com"},
            ...     {"username": "charlie", "email": "charlie@example.com"}
            ... ])
            >>> print(count)  # 3

            >>> # Get created instances
            >>> users = await User.objects.bulk_create([...], returning=True)
            >>> for user in users:
            ...     print(user.id, user.username)

            >>> # Ignore conflicts (skip duplicates)
            >>> await User.objects.bulk_create(
            ...     [{"email": "existing@example.com"}, {"email": "new@example.com"}],
            ...     ignore_conflicts=True
            ... )

            >>> # Upsert - update on conflict
            >>> await Product.objects.bulk_create(
            ...     [{"sku": "ABC123", "price": 29.99, "stock": 100}],
            ...     update_conflicts=True,
            ...     unique_fields=["sku"],
            ...     update_fields=["price", "stock"]
            ... )

        Note:
            Performance: O(n) where n is number of objects. Single database
            round-trip regardless of count. 10-100x faster than individual
            creates. Use returning=False for maximum speed when you don't need
            instances back. Batch size limits may apply (typically 1000-10000).
        """
        writer = QueryWriter(self)
        return await writer.bulk_create(
            objects=objects,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
            returning=returning,
        )

    async def update(self, returning: bool = False, **kwargs) -> list[Any] | int:
        """Update all matching rows with new field values.

        Executes UPDATE query on all rows matching current filters. Does not
        load instances into memory - operates directly on database. Returns
        count of rows modified.

        Args:
            returning: If True, return updated instances instead of count.
            **kwargs: Field=value pairs to update.

        Returns:
            Number of rows updated, or list of updated instances if returning=True.

        Raises:
            DatabaseError: If update fails (constraint violation, etc.).
            ModelAttributeError: If field doesn't exist.

        Example:
            >>> # Update all matching rows
            >>> count = await User.objects.filter(active=False).update(active=True)
            >>> print(f"Activated {count} users")

            >>> # Update with expressions
            >>> from async_orm_kit.query.expressions import F
            >>> await Product.objects.filter(category="sale").update(
            ...     price=F("price") * 0.8
            ... )

            >>> # Get updated instances
            >>> users = await User.objects.filter(role="guest").update(
            ...     role="user",
            ...     returning=True
            ... )

            >>> # Update single object
            >>> await User.objects.filter(id=123).update(last_login=datetime.now())

        Note:
            Performance: Single UPDATE query regardless of row count. O(n) where
            n is matching rows. Much faster than loading, modifying, and saving
            instances. Does not trigger model save() hooks or validation.
            Use returning=False for maximum performance.
        """
        writer = QueryWriter(self)
        count = await writer.update(returning=returning, **kwargs)
        return count

    async def delete(self) -> int:
        """Delete all matching rows from the database.

        Executes DELETE query on all rows matching current filters. Does not
        load instances into memory - operates directly on database. Returns
        count of rows deleted. Irreversible operation.

        Returns:
            Number of rows deleted.

        Raises:
            DatabaseError: If delete fails (foreign key constraint, etc.).

        Example:
            >>> # Delete all matching rows
            >>> count = await User.objects.filter(active=False).delete()
            >>> print(f"Deleted {count} inactive users")

            >>> # Delete single object
            >>> await User.objects.filter(id=123).delete()

            >>> # Delete with relationship filters
            >>> await Post.objects.filter(author__username="spam_user").delete()

            >>> # Delete all (use with caution!)
            >>> await User.objects.all().delete()

        Note:
            Performance: Single DELETE query regardless of row count. O(n) where
            n is matching rows. Does not trigger model delete() hooks. Foreign
            key constraints may prevent deletion - handle DatabaseError.
            Consider soft deletes (update deleted_at field) for audit trails.
        """
        writer = QueryWriter(self)
        return await writer.delete()

    def sql(self, dialect=None) -> str:
        """Generate raw SQL string for the current query without executing it.

        Compiles the QuerySet into SQL for debugging, logging, or analysis.
        Useful for understanding query performance and verifying generated SQL.

        Args:
            dialect: Optional SQLAlchemy dialect for database-specific syntax.
                    If None, uses the session's bound engine dialect.

        Returns:
            SQL string with parameter placeholders.

        Example:
            >>> # View generated SQL
            >>> qs = User.objects.filter(active=True).order_by("-created_at")
            >>> print(qs.sql())
            SELECT users.id, users.username, users.active, users.created_at
            FROM users
            WHERE users.active = :active_1
            ORDER BY users.created_at DESC

            >>> # For specific database dialect
            >>> from sqlalchemy.dialects import postgresql
            >>> print(qs.sql(dialect=postgresql.dialect()))

            >>> # Debug complex queries
            >>> complex_qs = Order.objects.select_related("customer").annotate(
            ...     total=Sum("items__price")
            ... ).filter(total__gte=100)
            >>> logger.debug(f"Query: {complex_qs.sql()}")

        Note:
            Performance: O(1) - only compiles query, doesn't execute.
            Parameters are shown as placeholders (:param_1), not actual values.
            Use for debugging and optimization, not for manual execution.
        """
        executor = QueryExecutor(self)
        return executor.sql(dialect=dialect)

    async def explain(self, analyze: bool = False, verbose: bool = False) -> str:
        """Execute EXPLAIN to show database query execution plan.

        Runs EXPLAIN (or EXPLAIN ANALYZE) to reveal how the database will
        execute the query. Essential for performance optimization and index
        tuning. Shows table scans, index usage, join methods, and cost estimates.

        Args:
            analyze: If True, actually execute query and include runtime stats    (PostgreSQL/MySQL). Shows actual vs estimated rows.
            verbose: If True, include additional detail (PostgreSQL only).
                    Shows output column list and other internal details.

        Returns:
            Query execution plan as formatted string.

        Raises:
            DatabaseError: If EXPLAIN execution fails.

        Example:
            >>> # Basic explain
            >>> plan = await User.objects.filter(active=True).explain()
            >>> print(plan)
            Seq Scan on users  (cost=0.00..35.50 rows=10 width=524)
              Filter: (active = true)

            >>> # With runtime statistics
            >>> plan = await User.objects.filter(email="john@example.com").explain(
            ...     analyze=True
            ... )
            >>> print(plan)
            Index Scan using users_email_idx on users
              (cost=0.29..8.30 rows=1 width=524)
              (actual time=0.015..0.016 rows=1 loops=1)

            >>> # Verbose output (PostgreSQL)
            >>> plan = await Post.objects.select_related("author").explain(
            ...     verbose=True
            ... )

            >>> # Identify missing indexes
            >>> plan = await Order.objects.filter(
            ...     created_at__gte=date.today()
            ... ).explain()
            >>> if "Seq Scan" in plan:
            ...     logger.warning("Missing index on created_at")

        Note:
            Performance: analyze=False is fast (O(1)) - only plans, doesn't execute.
            analyze=True executes the full query - use carefully on expensive queries.
            Look for "Seq Scan" (table scan) vs "Index Scan" to identify optimization
            opportunities. Cost estimates help compare query alternatives.
        """
        executor = QueryExecutor(self)
        return await executor.explain(analyze=analyze, verbose=verbose)
