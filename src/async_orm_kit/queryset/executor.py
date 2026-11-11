from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, inspect as sa_inspect, select
from sqlalchemy.exc import MultipleResultsFound, NoResultFound

from async_orm_kit._logging import get_logger
from async_orm_kit.exceptions import (
    DatabaseError,
    ModelAttributeError,
    RelationshipError,
)
from async_orm_kit.observers.mixin import ObserverMixin

if TYPE_CHECKING:
    from async_orm_kit.queryset.queryset import QuerySet

logger = get_logger(__name__)


class QueryExecutor(ObserverMixin):
    """Executes terminal QuerySet operations that fetch data from the database.Handles all read operations that trigger actual database queries: fetching results,
    streaming, counting, and existence checks. Transforms SQLAlchemy results based on
    QueryState configuration (model instances, dicts, tuples). Integrates with observer
    pattern for query lifecycle hooks.

    This class separates execution concerns from query building, allowing QuerySet to
    remain focused on query construction while QueryExecutor handles database interaction,
    result transformation, and error handling.
    Attributes:
        queryset: Parent QuerySet instance being executed.
        _query_state: QueryState with filters, joins, projections, etc.
        _compiler: QueryCompiler that converts QueryState to SQLAlchemy statements.
        _session: AsyncSession for database operations.
        _observers: List of observers to notify on query lifecycle events.

    Example:
        >>> # QueryExecutor is internal - users interact via QuerySet
        >>> from your_app.models import DBUser
        >>> qs = DBUser.objects.filter(active=True)
        >>>
        >>> # Under the hood, QuerySet delegates to QueryExecutor
        >>> executor = QueryExecutor(qs)
        >>> results = await executor.fetch_all()
        >>>
        >>> # Different result formats
        >>> await executor.count()  # Returns: 42
        >>> await executor.exists()  # Returns: True
        >>> await executor.first()  # Returns: User instance or None

    Note:
        Performance: All methods trigger database queries. Use count() instead of
        len(await fetch_all()) for efficiency. Stream large result sets with stream()
        to avoid loading everything into memory.
    """

    def __init__(self, queryset: QuerySet):
        """Initialize executor with QuerySet components.

        Extracts state, compiler, session, and observers from the parent QuerySet
        for use in execution methods.

        Args:
            queryset: QuerySet instance to execute operations for.
        """
        self.queryset = queryset
        self._query_state = queryset.state
        self._compiler = queryset.compiler
        self._session = queryset.session
        self._observers = queryset.observers or []

    def _ensure_lock_support(self) -> None:
        """Validate explicit row locking before opening a cursor."""
        from async_orm_kit.exceptions import QueryStateError

        if getattr(self._query_state, "for_update", None) is None:
            return
        dialect = self._session.get_bind(mapper=self.queryset.model).dialect.name
        if dialect not in {"postgresql", "mysql"}:
            raise NotImplementedError(
                f"select_for_update is not supported by the {dialect} dialect"
            )
        active = getattr(self._session, "in_transaction", None)
        if not callable(active) or not active():
            raise QueryStateError(
                "select_for_update requires a caller-owned active transaction"
            )

    async def stream(self):
        """Stream results one at a time without loading all into memory.

        Yields results incrementally using database cursor streaming. More memory-efficient
        than fetch_all() for large result sets. Respects QueryState configuration for
        result format (models, dicts, tuples). Triggers pre_stream and post_stream
        observer hooks.

        Yields:
            Results in format determined by QueryState:- Model instances (default)
            - Dicts (if values_select is set)
            - Tuples or flat values (if values_list_select is set)
            - Model instances with annotations attached as attributesRaises:
            DatabaseError: If streaming fails due to connection issues or invalid query.
            RelationshipError: If relationship path in query is invalid.

        Example:
            >>> # Stream large result set
            >>> async for user in User.objects.filter(active=True).stream():
            ...     await process_user(user)
            ...     # Only one user in memory at a time

            >>> # Stream with projections
            >>> async for username in User.objects.values_list("username", flat=True).stream():
            ...     print(username)

            >>> # Stream with annotations
            >>> qs = User.objects.annotate(post_count=Count("posts"))
            >>> async for user in qs.stream():
            ...     print(f"{user.username}: {user.post_count} posts")

        Note:
            Performance: O(1) memory regardless of result set size. Database maintains
            cursor state - don't use for queries that need to be held open long-term.
            Streaming may hold database connections longer than fetch_all().
        """
        self._ensure_lock_support()
        logger.debug(f"Streaming query for {self.queryset.model.__name__}")
        await self._trigger_observers(
            self._observers,
            "pre_stream",
            queryset=self.queryset,
            model=self.queryset.model,
        )

        try:
            stmt = self._compiler.compile(self._query_state)
            stream_result = await self._session.stream(stmt)
        except Exception as exc:
            raise DatabaseError("Error opening query stream", cause=exc) from exc

        try:
            count = 0

            # Handle different query modes.
            if self._query_state.values_select is not None:
                field_names = [
                    field_name for field_name, col in self._query_state.values_select
                ]
                if self._query_state.annotations:
                    field_names.extend(
                        name
                        for name in self._query_state.annotations
                        if name not in field_names
                    )

                async for row in stream_result:
                    count += 1
                    yield dict(zip(field_names, row, strict=False))

            elif self._query_state.values_list_select is not None:
                async for row in stream_result:
                    count += 1
                    yield row[0] if self._query_state.values_list_flat else tuple(row)

            elif self._query_state.annotations:
                async for row in stream_result.unique():
                    instance = row[0]
                    for i, name in enumerate(
                        self._query_state.annotations.keys(), start=1
                    ):
                        setattr(instance, name, row[i])
                    count += 1
                    yield instance

            else:
                async for obj in stream_result.unique().scalars():
                    count += 1
                    yield obj

            await self._trigger_observers(
                self._observers,
                "post_stream",
                queryset=self.queryset,
                model=self.queryset.model,
                count=count,
            )
        except Exception as e:
            logger.error("Stream failed")
            raise DatabaseError(
                message="Error streaming query",
                cause=e,
            ) from e
        finally:
            # AsyncSession.stream() owns a live cursor/connection until its
            # AsyncResult is closed. Async generators may be closed early by
            # callers (or interrupted by cancellation), so cleanup must not
            # depend on reaching the end of the result iterator.
            try:
                await stream_result.close()
            except Exception:
                logger.warning("Failed to close streamed query result", exc_info=True)

    async def fetch_all(self) -> list[Any]:
        """Fetch all matching results into memory.

        Executes query and loads all results at once. Result format depends on QueryState
        configuration: model instances (default), dicts (values), or tuples (values_list).Annotations are attached as attributes on model instances or included in projections.
        Triggers pre_execute and post_execute observer hooks.

        Returns:
            List of results in format determined by QueryState:
            - list[Model]: Model instances (default)
            - list[dict[str, Any]]: Dicts with field names as keys (values mode)
            - list[tuple] | list[Any]: Tuples or flat list (values_list mode)

        Raises:
            DatabaseError: If query execution fails.
            RelationshipError: If relationship path in query is invalid.

        Example:
            >>> # Fetch model instances
            >>> users = await User.objects.filter(active=True).fetch_all()
            >>> # Returns: [User(id=1, ...), User(id=2, ...)]

            >>> # Fetch as dicts
            >>> users = await User.objects.values("id", "username").fetch_all()
            >>> # Returns: [{"id": 1, "username": "john"}, ...]

            >>> # Fetch as tuples
            >>> usernames = await User.objects.values_list("username", flat=True).fetch_all()
            >>> # Returns: ["john", "jane", "alice"]

            >>> # With annotations
            >>> users = await User.objects.annotate(post_count=Count("posts")).fetch_all()
            >>> print(users[0].post_count)  # Annotation attached as attribute

        Note:
            Performance: O(n) memory where n is result count. Loads entire result set
            into memory - use stream() for large datasets. Prefer count() over
            len(fetch_all()) for counting. Use exists() instead of bool(fetch_all())
            for existence checks.
        """
        await self._trigger_observers(
            self._observers,
            "pre_execute",
            queryset=self.queryset,
            model=self.queryset.model,
            operation="fetch",
        )

        self._ensure_lock_support()
        logger.debug(f"Executing query for {self._query_state.model.__name__}")
        try:
            query = self._compiler.compile(self._query_state)
            result: Any = await self._session.execute(query)

            # values() - return dictionaries
            if self._query_state.values_select is not None:
                rows = result.all()

                # Extract field names from tuples
                field_names = [
                    field_name for field_name, col in self._query_state.values_select
                ]

                # Add annotation names if present
                if self._query_state.annotations:
                    field_names.extend(
                        name
                        for name in self._query_state.annotations
                        if name not in field_names
                    )

                result = [dict(zip(field_names, row, strict=False)) for row in rows]

            # values_list() - return tuples or flat list
            elif self._query_state.values_list_select is not None:
                rows = result.all()

                # For flat mode, ensure we're only returning the single field
                if self._query_state.values_list_flat:
                    result = [row[0] for row in rows]
                else:
                    # For tuple mode, return all columns (fields + annotations)
                    result = [tuple(row) for row in rows]

            # Model instances with annotations attached
            elif self._query_state.annotations:
                rows = result.unique().all()
                instances = []
                for row in rows:
                    instance = row[0]
                    # Attach annotation values to the instance
                    for i, name in enumerate(
                        self._query_state.annotations.keys(), start=1
                    ):
                        setattr(instance, name, row[i])
                    instances.append(instance)
                result = instances
            else:
                # joinedload() of collection paths can duplicate root rows.
                # unique() is also harmless for scalar-only eager loading.
                result = list(result.unique().scalars().all())

            await self._trigger_observers(
                self._observers,
                "post_execute",
                queryset=self.queryset,
                model=self.queryset.model,
                operation="fetch",
                results=result,
                count=len(result),
            )
            return result
        except RelationshipError:
            raise

        except Exception as e:
            logger.error("Query execution failed")
            raise DatabaseError(message="Error executing query", cause=e) from e

    async def first(self) -> Any | None:
        """Return first result or None if no results match.

        Convenience method that fetches all results and returns the first one. More
        efficient than manually indexing fetch_all() results. Returns None instead
        of raising an exception when no results exist.

        Returns:
            First result in format determined by QueryState, or None if no results.
            Format matches fetch_all() return type (model, dict, or tuple).
        Raises:
            DatabaseError: If query execution fails.
            RelationshipError: If relationship path in query is invalid.

        Example:
            >>> # Get first active user
            >>> user = await User.objects.filter(active=True).first()
            >>> if user:
            ...     print(user.username)

            >>> # With ordering
            >>> newest = await Post.objects.order_by("-created_at").first()

            >>> # Returns None if no results
            >>> admin = await User.objects.filter(role="admin").first()
            >>> # Returns: None (if no admins exist)

        Note:
            Performance: Fetches all results then returns first - not optimized with
            LIMIT 1. Consider using get() if you expect exactly one result, or add
            .limit(1) before calling first() for better performance on large result sets.
        """
        logger.debug(f"Executing .first() on {self._query_state.model.__name__}")
        try:
            results = await self.fetch_all()
            return results[0] if results else None
        except Exception as e:
            logger.error(".first() failed")
            raise DatabaseError(message="Error executing first()", cause=e) from e

    async def get(self) -> Any:
        """Get single object matching criteria, raising exception if not exactly one.

        Fetches exactly one result, raising model-specific exceptions if zero or multiple
        results are found. Use when you expect exactly one match (e.g., fetching by
        primary key or unique field). More strict than first() which returns None.

        Returns:
            Single model instance matching the query.
        Raises:
            Model.DoesNotExist: If no results match the query.
            Model.MultipleObjectsReturned: If more than one result matches.
            DatabaseError: If query execution fails.
            ModelAttributeError: If field in query doesn't exist.

        Example:
            >>> # Get by primary key
            >>> user = await User.objects.filter(id=1).get()
            >>> # Raises DoesNotExist if id=1 doesn't exist

            >>> # Get by unique field
            >>> user = await User.objects.filter(email="john@example.com").get()
            >>> # Raises MultipleObjectsReturned if multiple users have same email

            >>> # Handle exceptions
            >>> try:
            ...     user = await User.objects.filter(username="john").get()
            ... except User.DoesNotExist:
            ...     print("User not found")
            ... except User.MultipleObjectsReturned:
            ...     print("Multiple users found")

        Note:
            Performance: O(1) database query. More efficient than fetch_all() for
            single-result queries. Exceptions are model-specific (User.DoesNotExist,
            not a generic exception), allowing targeted error handling.
        """
        self._ensure_lock_support()
        try:
            compiled = self._compiler.compile(self._query_state)
            result = await self._session.execute(compiled)
            result = result.unique()
            if self._query_state.values_select is not None:
                row = result.one()
                field_names = [name for name, _ in self._query_state.values_select]
                field_names.extend(
                    name
                    for name in self._query_state.annotations
                    if name not in field_names
                )
                return dict(zip(field_names, row, strict=False))
            if self._query_state.values_list_select is not None:
                row = result.one()
                return row[0] if self._query_state.values_list_flat else tuple(row)
            if self._query_state.annotations:
                row = result.one()
                instance = row[0]
                for index, name in enumerate(self._query_state.annotations, start=1):
                    setattr(instance, name, row[index])
                return instance
            return result.scalar_one()
        except NoResultFound as e:
            model = self._query_state.model
            logger.warning(f".get() not found for {model.__name__}")
            raise model.DoesNotExist(
                f"{model.__name__} matching query does not exist."
            ) from e

        except MultipleResultsFound as e:
            model = self._query_state.model
            logger.error(".get() returned multiple results for")
            raise model.MultipleObjectsReturned(
                f"get() returned multiple results: {str(e)}"
            ) from e

        except (ModelAttributeError, DatabaseError):
            raise

        except Exception as e:
            logger.error(".get() failed")
            raise DatabaseError(message="Error executing get query", cause=e) from e

    async def count(self) -> int:
        """Return count of matching records without fetching them.

        Executes COUNT(*) query at database level. More efficient than len(fetch_all())
        as it doesn't load actual data. Respects all filters, joins, and HAVING conditions
        but ignores ordering and projections. Triggers pre_execute and post_execute
        observer hooks.

        Returns:
            Number of rows matching the query as integer.

        Raises:
            DatabaseError: If count query execution fails.

        Example:
            >>> # Count all users
            >>> total = await User.objects.count()
            >>> # Returns: 1523

            >>> # Count with filters
            >>> active_count = await User.objects.filter(active=True).count()

            >>> # Count with aggregates
            >>> prolific = await User.objects.annotate(
            ...     post_count=Count("posts")
            ... ).filter(post_count__gte=10).count()

            >>> # More efficient than len()
            >>> # Bad: len(await User.objects.all())  # Loads all data
            >>> # Good: await User.objects.count()    # Database-level count

        Note:
            Performance: O(1) memory, O(n) database scan where n is table size (with
            optimizations for simple queries). Much faster than fetching and counting
            in Python. Use instead of len(fetch_all()) or bool(fetch_all()).
        """
        if getattr(self._query_state, "for_update", None) is not None:
            from async_orm_kit.exceptions import QueryStateError

            raise QueryStateError("Row locks cannot be combined with count()/exists()")

        await self._trigger_observers(
            self._observers,
            "pre_execute",
            queryset=self.queryset,
            model=self.queryset.model,
            operation="count",
        )
        try:
            compiled = self._compiler.compile(self._query_state)

            # Relationship filters can join a collection and produce several SQL
            # rows for one root model instance. QuerySet.count() follows model
            # result semantics, so count distinct root identities for ordinary
            # unsliced model queries. Grouped and projected queries count their
            # selected result rows instead.
            state = self._query_state
            count_root_models = (
                state.values_select is None
                and state.values_list_select is None
                and not state.has_aggregates
                and not state.distinct_on
                and state.limit_val is None
                and state.offset_val is None
            )
            if count_root_models:
                inspected = sa_inspect(state.model)
                mapper = inspected.mapper if inspected.is_aliased_class else inspected
                primary_key = [
                    getattr(state.model, mapper.get_property_by_column(col).key)
                    for col in mapper.primary_key
                ]
                count_source = (
                    compiled.with_only_columns(
                        *primary_key,
                        maintain_column_froms=True,
                    )
                    .order_by(None)
                    .distinct()
                    .subquery()
                )
            else:
                count_source = compiled.subquery()

            count_query = select(func.count()).select_from(count_source)
            result = await self._session.execute(count_query)
            count = result.scalar_one()
            await self._trigger_observers(
                self._observers,
                "post_execute",
                queryset=self.queryset,
                model=self.queryset.model,
                operation="count",
                count=count,
            )
            return count
        except Exception as e:
            logger.error(".count() failed")
            raise DatabaseError(message="Error executing count()", cause=e) from e

    async def exists(self) -> bool:
        """Check if any records match the query without fetching them.

        Executes optimized existence check using SELECT 1 with LIMIT 1. More efficient
        than bool(fetch_all()) or count() > 0 as it stops after finding first match.
        Respects all filters and joins. Triggers pre_execute and post_execute observer
        hooks.

        Returns:
            True if at least one row matches the query, False otherwise.

        Raises:
            DatabaseError: If existence check fails.

        Example:
            >>> # Check if any active users exist
            >>> has_active = await User.objects.filter(active=True).exists()
            >>> # Returns: True or False

            >>> # More efficient than alternatives
            >>> # Bad: bool(await User.objects.filter(active=True).fetch_all())
            >>> # Bad: await User.objects.filter(active=True).count() > 0
            >>> # Good: await User.objects.filter(active=True).exists()

            >>> # Use in conditionals
            >>> if await User.objects.filter(email=email).exists():
            ...     raise ValueError("Email already registered")

        Note:
            Performance: O(1) memory, stops at first match. Most efficient way to
            check existence. Uses LIMIT 1 internally - database stops scanning after
            finding first row. Prefer over count() > 0 or bool(fetch_all()).
        """
        if getattr(self._query_state, "for_update", None) is not None:
            from async_orm_kit.exceptions import QueryStateError

            raise QueryStateError("Row locks cannot be combined with count()/exists()")

        await self._trigger_observers(
            self._observers,
            "pre_execute",
            queryset=self.queryset,
            model=self.queryset.model,
            operation="exists",
        )
        try:
            compiled = self._compiler.compile(self._query_state)
            exists_query = select(1).select_from(compiled.subquery()).limit(1)
            result = await self._session.execute(exists_query)
            exists = result.scalar() is not None
            await self._trigger_observers(
                self._observers,
                "post_execute",
                queryset=self.queryset,
                model=self.queryset.model,
                operation="exists",
                exists=exists,
            )
            return exists
        except Exception as e:
            logger.error(".exists() failed")
            raise DatabaseError(message="Error checking existence", cause=e) from e

    def sql(self, dialect: Any | None = None) -> str:
        """Return compiled SQL string for the current query.

        Compiles QueryState to SQL with bound-parameter placeholders. Useful for
        debugging and logging without exposing sensitive filter values. Does not
        execute the query.

        Args:
            dialect: Optional SQLAlchemy dialect instance (e.g., postgresql.dialect(),
                    mysql.dialect()). If None, uses the session's bound engine dialect.

        Returns:
            SQL string with dialect-specific parameter placeholders. Bound values
            are not included in the returned SQL.

        Example:
            >>> # Get SQL for current query
            >>> qs = User.objects.filter(active=True, age__gte=18)
            >>> print(qs.sql())
            >>> # SELECT users.id, users.username, users.age
            >>> # FROM users
            >>> # WHERE users.active = ? AND users.age >= ?
            >>> # Specify dialect
            >>> from sqlalchemy.dialects import postgresql
            >>> print(qs.sql(dialect=postgresql.dialect()))

            >>> # Debug complex queries
            >>> qs = User.objects.annotate(
            ...     post_count=Count("posts")
            ... ).filter(post_count__gte=5).order_by("-post_count")
            >>> print(qs.sql())

        Note:
            Performance: O(1) - just string compilation, no database query. Bound values
            are intentionally omitted. Use for debugging, not for manual execution of raw SQL.
        """
        stmt = self._compiler.compile(self._query_state)

        if dialect is None:
            dialect = self._session.get_bind(mapper=self.queryset.model).dialect

        return str(stmt.compile(dialect=dialect))

    async def explain(self, analyze: bool = False, verbose: bool = False) -> str:
        """Execute a parameterized EXPLAIN statement and return the query plan.

        SQL values remain DBAPI parameters; they are never interpolated into SQL.
        SQLite supports EXPLAIN QUERY PLAN only. PostgreSQL supports ANALYZE and
        VERBOSE options. MySQL supports EXPLAIN ANALYZE, but not VERBOSE here.
        """
        if type(analyze) is not bool or type(verbose) is not bool:
            raise TypeError("analyze and verbose must be bool values.")

        bind = self._session.get_bind()
        dialect = bind.dialect
        dialect_name = dialect.name

        if dialect_name == "sqlite":
            if analyze or verbose:
                raise NotImplementedError(
                    "SQLite EXPLAIN QUERY PLAN does not support analyze or verbose."
                )
            prefix = "EXPLAIN QUERY PLAN"
        elif dialect_name == "postgresql":
            options = []
            if analyze:
                options.append("ANALYZE")
            if verbose:
                options.append("VERBOSE")
            prefix = f"EXPLAIN ({', '.join(options)})" if options else "EXPLAIN"
        elif dialect_name == "mysql":
            if verbose:
                raise NotImplementedError("verbose EXPLAIN is not supported for MySQL.")
            prefix = "EXPLAIN ANALYZE" if analyze else "EXPLAIN"
        elif dialect_name == "mariadb":
            if analyze or verbose:
                raise NotImplementedError(
                    "ANALYZE/VERBOSE EXPLAIN is not configured for MariaDB."
                )
            prefix = "EXPLAIN"
        else:
            raise NotImplementedError(
                f"EXPLAIN is not configured for {dialect_name!r}."
            )

        statement = self._compiler.compile(self._query_state)
        compiled = statement.compile(
            dialect=dialect,
            compile_kwargs={"render_postcompile": True},
        )
        parameters = (
            tuple(compiled.params[name] for name in compiled.positiontup)
            if compiled.positional
            else compiled.params
        )

        try:
            connection = await self._session.connection()
            result = await connection.exec_driver_sql(
                f"{prefix} {compiled.string}",
                parameters,
            )
            rows = result.fetchall()
            return "\n".join(
                str(row[-1] if dialect_name == "sqlite" else row[0]) for row in rows
            )
        except Exception as exc:
            logger.error("EXPLAIN execution failed")
            raise DatabaseError(
                message="Error explaining query",
                cause=exc,
            ) from exc
