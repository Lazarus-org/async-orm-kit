from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import (
    delete as sql_delete,
    insert as sql_insert,
    update as sql_update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.inspection import inspect as sqla_inspect

from async_orm_kit._logging import get_logger
from async_orm_kit.exceptions import DatabaseError, ModelAttributeError
from async_orm_kit.observers.mixin import ObserverMixin

if TYPE_CHECKING:
    from async_orm_kit.queryset.queryset import QuerySet

logger = get_logger(__name__)


class QueryWriter(ObserverMixin):
    """Handles write operations for QuerySet including create, update, and delete.

    Manages all database write operations with support for bulk operations, conflict
    handling (upserts), and observer notifications. Separates write concerns from
    query building, allowing QuerySet to focus on read operations while QueryWriter
    handles data modification with proper transaction management and error handling.
    Attributes:
        queryset: Parent QuerySet instance.
        _model: SQLAlchemy model class being operated on.
        query_state: QueryState with filters and conditions.
        _compiler: QueryCompiler for converting state to SQL.
        _session: AsyncSession for database operations.
        _observers: List of observers to notify on write events.
        _pk_field: Primary key field name extracted from model.

    Example:
        >>> # QueryWriter is internal - users interact via QuerySet
        >>> from your_app.models import DBUser
        >>>
        >>> # Create single instance
        >>> user = await DBUser.objects.create(username="john", email="john@example.com")
        >>>
        >>> # Bulk create
        >>> users = await DBUser.objects.bulk_create([
        ...     {"username": "alice", "email": "alice@example.com"},
        ...     {"username": "bob", "email": "bob@example.com"}
        ... ], returning=True)
        >>>
        >>> # Update matching records
        >>> count = await DBUser.objects.filter(active=False).update(active=True)
        >>>
        >>> # Delete matching records
        >>> deleted = await DBUser.objects.filter(created_at__lt=cutoff_date).delete()

    Note:
        All mutations execute or flush within the supplied session's current
        transaction. This writer never commits or rolls back; transaction ownership
        remains with the application. Conflict handling syntax is dialect-specific.
    """

    def __init__(self, queryset: QuerySet):
        """Initialize writer with QuerySet components.

        Extracts model, state, compiler, session, and observers from parent QuerySet.
        Determines primary key field for use in bulk operations and observer notifications.

        Args:
            queryset: QuerySet instance to execute write operations for.

        Raises:
            ValueError: If model has no primary key defined.
        """
        self.queryset = queryset
        self._model = queryset.model
        self.query_state = queryset.state
        if self.query_state.for_update is not None:
            from async_orm_kit.exceptions import QueryStateError

            raise QueryStateError("select_for_update applies to reads only")
        self._compiler = queryset.compiler
        self._session = queryset.session
        self._observers = queryset.observers
        self._pk_field = self._get_primary_key_field()

    def _validate_mutation_selection(self) -> None:
        """Reject SELECT constraints that cannot be preserved by portable DML."""
        from async_orm_kit.exceptions import QueryStateError

        state = self.query_state
        if (
            state.limit_val is not None
            or state.offset_val is not None
            or state.has_aggregates
            or state.group_by_fields
            or state.having_conditions
            or state.is_distinct
            or state.distinct_on
        ):
            raise QueryStateError(
                "Bulk update/delete require an unsliced, ungrouped, non-distinct QuerySet; "
                "select primary keys first and filter a fresh QuerySet explicitly."
            )
        if state.join_graph.join_order:
            raise NotImplementedError(
                "Bulk update/delete with joined relationship filters are not supported; "
                "use a correlated subquery or explicit primary-key selection."
            )

    def _get_primary_key_field(self) -> str:
        """Get the primary key field name from model.Inspects SQLAlchemy model mapper to extract primary key column name.
        Required for bulk operations and observer notifications that need to
        identify specific records.

        Returns:
            Primary key field name as string.

        Raises:
            ValueError: If model has no primary key defined.
        Example:
            >>> writer = QueryWriter(User.objects)
            >>> pk = writer._get_primary_key_field()
            >>> # Returns: "id"

        Note:
            Performance: O(1) - cached by SQLAlchemy mapper. Only supports single-column
            primary keys - composite keys use first column only.
        """
        mapper = sqla_inspect(self._model)
        pk_columns = mapper.primary_key
        return mapper.get_property_by_column(pk_columns[0]).key

    async def create(self, **kwargs):
        """Create and save a new model instance.

        Creates a single model instance with provided field values and flushes it
        in the current session transaction. Prefers the model's save() lifecycle when
        available. Automatically refreshes instance to load database-generated values
        (auto-increment IDs, defaults, triggers).

        Args:
            **kwargs: Field names and values for the new instance.

        Returns:
            Created and persisted model instance with all database-generated values.

        Raises:
            DatabaseError: If creation fails due to constraint violations, invalid
                          fields, or database errors.
            ModelAttributeError: If kwargs contain fields that don't exist on model.

        Example:
            >>> # Create with required fields
            >>> user = await User.objects.create(
            ...     username="john",
            ...     email="john@example.com",
            ...     password_hash="..."
            ... )
            >>> print(user.id)  # Auto-generated ID available
            >>>
            >>> # Database defaults applied
            >>> user = await User.objects.create(username="alice")
            >>> print(user.created_at)  # Timestamp from database default
        Note:
            The model is flushed but not committed. The caller owns the transaction.
        """
        instance = self._model(**kwargs)

        # First try using the model's save() method
        if hasattr(instance, "save") and callable(instance.save):
            await instance.save(self._session)
            logger.debug(
                f"Created {self._model.__name__}(pk={getattr(instance, self._pk_field)} via save())"
            )
            return instance

        # Fallback to the original approach
        try:
            self._session.add(instance)
            await self._session.flush()
            await self._session.refresh(instance)

            logger.debug(
                f"Created {self._model.__name__}(pk={getattr(instance, self._pk_field)})"
            )
            return instance

        except Exception as e:
            logger.error("Create failed")
            raise DatabaseError(
                message=f"Error creating {self._model.__name__}", cause=e
            ) from e

    async def bulk_create(
        self,
        objects: list[dict],
        *,
        returning: bool = False,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: list[str] | None = None,
        unique_fields: list[str] | None = None,
    ) -> list[Any] | int:
        """Bulk create multiple instances with optional conflict handling.

        Efficiently inserts multiple records in a single database operation. Supports
        upsert behavior (INSERT ... ON CONFLICT) for handling unique constraint violations.
        Can either ignore conflicts or update existing records on conflict. Database-specific
        syntax used for PostgreSQL, MySQL, and SQLite.

        Args:
            objects: List of dicts with field names as keys and values to insert.
            returning: If True, return created instances instead of count. Required
                      for observer notifications.
            ignore_conflicts: Skip rows that violate unique constraints without error.
                             Mutually exclusive with update_conflicts.
            update_conflicts: Update existing rows on unique constraint violation (upsert).
                             Requires unique_fields and update_fields.
            update_fields: Fields to update when conflict occurs. Required if
                          update_conflicts=True.
            unique_fields: Constraint columns that define conflict target. Required if
                          update_conflicts=True.

        Returns:
            If returning=True: List of created/updated model instances.
            If returning=False: Integer count of affected rows.

        Raises:
            ValueError: If update_conflicts=True without unique_fields/update_fields,
                       or if ignore_conflicts and update_conflicts both True.
            DatabaseError: If bulk insert fails due to database errors.
            NotImplementedError: If conflict handling requested on unsupported database.

        Example:
            >>> # Simple bulk create
            >>> count = await User.objects.bulk_create([
            ...     {"username": "alice", "email": "alice@example.com"},
            ...     {"username": "bob", "email": "bob@example.com"}
            ... ])
            >>> # Returns: 2

            >>> # With returning to get instances
            >>> users = await User.objects.bulk_create([
            ...     {"username": "charlie", "email": "charlie@example.com"}
            ... ], returning=True)
            >>> print(users[0].id)  # Access created instance

            >>> # Ignore conflicts (skip duplicates)
            >>> count = await User.objects.bulk_create([
            ...     {"username": "alice", "email": "new@example.com"}
            ... ], ignore_conflicts=True)
            >>> # Skips if username already exists

            >>> # Upsert on conflict
            >>> users = await User.objects.bulk_create([
            ...     {"username": "alice", "email": "updated@example.com", "active": True}
            ... ],
            ... update_conflicts=True,
            ... unique_fields=["username"],
            ... update_fields=["email", "active"],
            ... returning=True)
            >>> # Updates email and active if username exists, inserts otherwise
        Note:
            Performance: O(n) where n is number of objects. Much faster than looping
            create() - single database round-trip. returning=True may have limited
            support with conflict handling on MySQL/SQLite. Conflict handling syntax
            varies: PostgreSQL uses ON CONFLICT, MySQL uses ON DUPLICATE KEY UPDATE,
            SQLite uses ON CONFLICT.
        """
        if not objects:
            return [] if returning else 0

        if update_conflicts and not (unique_fields and update_fields):
            raise ValueError(
                "update_conflicts requires both unique_fields and update_fields to be provided."
            )

        if ignore_conflicts and update_conflicts:
            raise ValueError(
                "ignore_conflicts and update_conflicts are mutually exclusive."
            )

        # MySQL 8.0 has no INSERT .. RETURNING. Never attempt to send invalid
        # SQL or silently infer which rows an upsert inserted/updated. Normal
        # returning inserts are implemented below through ORM flush instead.
        dialect_name = self._session.get_bind(mapper=self._model).dialect.name
        if (
            dialect_name == "mysql"
            and returning
            and (ignore_conflicts or update_conflicts)
        ):
            raise NotImplementedError(
                "MySQL 8 does not support bulk_create(returning=True) with "
                "ignore_conflicts or update_conflicts; use returning=False."
            )

        await self._trigger_observers(
            self._observers,
            "pre_bulk_create",
            objects=objects,
            model=self._model,
            session=self._session,
            returning=returning,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )
        mapper = sqla_inspect(self._model)
        column_names = {attribute.key for attribute in mapper.column_attrs}
        for row in objects:
            invalid_fields = set(row) - column_names
            if invalid_fields:
                raise ModelAttributeError(
                    f"Unknown fields for {self._model.__name__}: "
                    f"{', '.join(sorted(invalid_fields))}"
                )

        if update_conflicts:
            invalid_conflict_fields = (
                set(update_fields or ()) | set(unique_fields or ())
            ) - column_names
            if invalid_conflict_fields:
                raise ModelAttributeError(
                    f"Unknown conflict fields for {self._model.__name__}: "
                    f"{', '.join(sorted(invalid_conflict_fields))}"
                )

        column_key_by_attribute: dict[str, str] = {}
        for attribute in mapper.column_attrs:
            if len(attribute.columns) == 1:
                column_key_by_attribute[attribute.key] = attribute.columns[0].key

        if not returning:
            unsupported_fields = {
                field
                for row in objects
                for field in row
                if field not in column_key_by_attribute
            }
            if unsupported_fields:
                raise ModelAttributeError(
                    "Bulk create requires each mapped field to resolve to one column: "
                    f"{', '.join(sorted(unsupported_fields))}"
                )
            insert_target = self._model.__table__
            execution_objects = [
                {column_key_by_attribute[field]: value for field, value in row.items()}
                for row in objects
            ]
        else:
            insert_target = self._model
            execution_objects = objects

        if dialect_name == "mysql" and returning:
            # An ordinary MySQL INSERT has no native RETURNING; ORM flush
            # supplies per-instance Python defaults and generated primary keys.
            # This deliberately trades bulk efficiency for truthful returned
            # objects. Conflict-mode returning is rejected before any DML.
            mysql_instances = [self._model(**row) for row in objects]
            try:
                self._session.add_all(mysql_instances)
                await self._session.flush()
                # MySQL 8 cannot populate server-side defaults via RETURNING.
                # Explicit refresh ensures returned models have generated
                # timestamps and other server values loaded before access.
                for instance in mysql_instances:
                    await self._session.refresh(instance)
            except Exception as exc:
                logger.error("MySQL bulk insert with returned objects failed")
                raise DatabaseError(
                    f"Error bulk creating {self._model.__name__}", cause=exc
                ) from exc
            await self._trigger_observers(
                self._observers,
                "post_bulk_create",
                instances=mysql_instances,
                returning=True,
                model=self._model,
                session=self._session,
                count=len(mysql_instances),
            )
            return mysql_instances

        instances: list[Any] | None = None
        stmt: Any
        if ignore_conflicts or update_conflicts:
            if dialect_name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = pg_insert(insert_target)

                if ignore_conflicts:
                    stmt = stmt.on_conflict_do_nothing()
                else:
                    set_clause = {
                        column_key_by_attribute[field]: stmt.excluded[
                            column_key_by_attribute[field]
                        ]
                        for field in update_fields or ()
                    }
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[
                            self._model.__table__.c[column_key_by_attribute[field]]
                            for field in unique_fields or ()
                        ],
                        set_=set_clause,
                    )

            elif dialect_name == "mysql":
                from sqlalchemy.dialects.mysql import insert as mysql_insert

                stmt = mysql_insert(insert_target)

                if ignore_conflicts:
                    stmt = stmt.prefix_with("IGNORE")
                else:
                    set_clause = {
                        column_key_by_attribute[field]: stmt.inserted[
                            column_key_by_attribute[field]
                        ]
                        for field in update_fields or ()
                    }
                    stmt = stmt.on_duplicate_key_update(**set_clause)

            elif dialect_name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                stmt = sqlite_insert(insert_target)

                if ignore_conflicts:
                    stmt = stmt.on_conflict_do_nothing()
                else:
                    set_clause = {
                        column_key_by_attribute[field]: stmt.excluded[
                            column_key_by_attribute[field]
                        ]
                        for field in update_fields or ()
                    }
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[
                            self._model.__table__.c[column_key_by_attribute[field]]
                            for field in unique_fields or ()
                        ],
                        set_=set_clause,
                    )

            else:
                raise NotImplementedError(
                    f"Conflict handling is not supported for {dialect_name}. "
                    f"Supported dialects: postgresql, mysql, sqlite"
                )
        else:
            stmt = sql_insert(insert_target)

        logger.debug(f"Bulk creating {len(objects)} {self._model.__name__} instances")
        try:
            if returning:
                if dialect_name in ("mysql", "sqlite") and (
                    ignore_conflicts or update_conflicts
                ):
                    logger.warning(
                        f"returning=True with conflict handling may have limited support on {dialect_name}"
                    )
                stmt = stmt.returning(self._model)
                if update_conflicts:
                    # An upsert may return a row already present in the session's
                    # identity map. Refresh it from RETURNING so callers receive
                    # the database's updated values rather than stale attributes.
                    result = await self._session.execute(
                        stmt,
                        execution_objects,
                        execution_options={"populate_existing": True},
                    )
                else:
                    result = await self._session.execute(stmt, execution_objects)
                instances = list(result.scalars().all())
                count = len(instances)
            else:
                # Preserve ORM execute's normal autoflush behavior before
                # executing Core executemany on the current session connection.
                await self._session.flush()
                # Core executemany preserves SQLAlchemy column defaults and
                # exposes CursorResult.rowcount (ORM bulk INSERT results do not).
                connection = await self._session.connection()
                if dialect_name == "postgresql":
                    # asyncpg executemany reports -1 for INSERT. RETURNING the
                    # identities counts rows actually inserted/updated, including
                    # ON CONFLICT DO NOTHING, without hydrating ORM instances.
                    result = await connection.execute(
                        stmt.returning(*mapper.primary_key), execution_objects
                    )
                    count = len(result.fetchall())
                else:
                    result = await connection.execute(
                        stmt.execution_options(preserve_rowcount=True),
                        execution_objects,
                    )
                    count = cast(CursorResult[Any], result).rowcount
                instances = None
        except Exception as exc:
            logger.error("Bulk create failed")
            raise DatabaseError(
                f"Error bulk creating {self._model.__name__}", cause=exc
            ) from exc

        await self._trigger_observers(
            self._observers,
            "post_bulk_create",
            instances=instances,
            returning=returning,
            model=self._model,
            session=self._session,
            count=count,
        )
        logger.debug(f"Bulk created {count} instances")
        if returning:
            return cast(list[Any], instances)
        return count

    async def update(self, returning: bool = False, **kwargs) -> list[Any] | int:
        """Update all records matching QuerySet filters.

        Executes UPDATE query on records matching current QuerySet filters. Updates
        specified fields to new values in a single database operation. More efficient
        than fetching instances and updating individually. Respects all QuerySet
        filters but ignores ordering and projections.

        Args:
            returning: If True, fetch and return updated instances. Must be True if
                      observers are attached, otherwise observers cannot be notified
                      with instance data.
            **kwargs: Field names and new values to update.

        Returns:
            If returning=True: List of updated model instances.
            If returning=False: Integer count of updated rows.

        Raises:
            ModelAttributeError: If kwargs contain fields that don't exist on model.
            DatabaseError: If update fails due to database errors or constraint violations.

        Example:
            >>> # Update matching records
            >>> count = await User.objects.filter(active=False).update(active=True)
            >>> # Returns: 15 (number of users updated)

            >>> # Update multiple fields
            >>> count = await User.objects.filter(
            ...     last_login__lt=cutoff_date
            ... ).update(active=False, deactivated_at=datetime.now())

            >>> # With returning to get updated instances
            >>> users = await User.objects.filter(role="user").update(
            ...     role="member",
            ...     returning=True
            ... )
            >>> for user in users:
            ...     print(f"Updated {user.username}")

        Note:
            Performance: O(1) database query regardless of number of matching rows.
            Much faster than fetching and updating in a loop. The statement remains
            in the caller-owned transaction. returning=True may impact
            performance on large updates.
        """
        self._validate_mutation_selection()
        if not kwargs:
            return [] if returning else 0

        # The normal MySQL 8 dialect does not provide UPDATE .. RETURNING.
        # A second SELECT would not reliably represent the exact changed-row
        # set under concurrency, so reject rather than returning stale values.
        dialect = self._session.get_bind(mapper=self._model).dialect
        if returning and dialect.name == "mysql":
            raise NotImplementedError(
                "MySQL 8 does not support QuerySet.update(returning=True); "
                "use returning=False."
            )

        await self._trigger_observers(
            self._observers,
            "pre_bulk_update",
            queryset=self.queryset,
            model=self._model,
            session=self._session,
            values=kwargs,
            returning=returning,
        )

        logger.debug(f"Updating {self._model.__name__}", fields=list(kwargs.keys()))
        try:
            from sqlalchemy.sql.elements import ClauseElement

            from async_orm_kit.query.lookups.resolver import ResolverContext
            from async_orm_kit.query.mixins.combinable import CombinableMixin
            from async_orm_kit.queryset.builder import QueryBuilder

            expression_builder = QueryBuilder(self.query_state)
            values = {}
            for key, value in kwargs.items():
                if not hasattr(self._model, key):
                    raise ModelAttributeError(
                        f"Model '{self._model.__name__}' has no field '{key}'"
                    )
                if isinstance(value, (CombinableMixin, ClauseElement)):
                    value, contains_aggregate = expression_builder._resolve_expression(
                        value, ResolverContext()
                    )
                    from async_orm_kit.query.expressions.analysis import contains_window

                    if contains_aggregate or contains_window(value):
                        raise ValueError(
                            "UPDATE values cannot include aggregate or window expressions"
                        )
                    if self.query_state.join_graph.join_order:
                        raise NotImplementedError(
                            "UPDATE expressions cannot traverse relationships"
                        )
                values[getattr(self._model, key)] = value

            compiled = self._compiler.compile(self.query_state)
            stmt = sql_update(self._model).values(values)
            if compiled.whereclause is not None:
                stmt = stmt.where(compiled.whereclause)

            if returning:
                result = await self._session.execute(stmt.returning(self._model))
                updated_instances = list(result.scalars().all())
                updated_count = len(updated_instances)
            else:
                result = await self._session.execute(stmt)
                updated_instances = None
                updated_count = cast(CursorResult[Any], result).rowcount
        except (ModelAttributeError, NotImplementedError, TypeError, ValueError):
            raise
        except Exception as exc:
            logger.error("Update failed")
            raise DatabaseError(
                message=f"Error updating {self._model.__name__}", cause=exc
            ) from exc

        await self._trigger_observers(
            self._observers,
            "post_bulk_update",
            instances=updated_instances,
            returning=returning,
            queryset=self.queryset,
            model=self._model,
            session=self._session,
            count=updated_count,
        )
        logger.debug(f"Updated {updated_count} instances")
        if returning:
            return cast(list[Any], updated_instances)
        return updated_count

    async def delete(self) -> int:
        """Delete all records matching QuerySet filters.

        Executes DELETE query on records matching current QuerySet filters. Permanently
        removes records from database in a single operation. If observers are attached,
        fetches primary keys before deletion for post-delete notifications. Respects
        all QuerySet filters but ignores ordering and projections.

        Returns:
            Integer count of deleted rows.

        Raises:
            DatabaseError: If deletion fails due to foreign key constraints or database errors.

        Example:
            >>> # Delete matching records
            >>> count = await User.objects.filter(active=False).delete()
            >>> # Returns: 23 (number of users deleted)

            >>> # Delete with complex filters
            >>> count = await Post.objects.filter(
            ...     created_at__lt=cutoff_date,
            ...     views__lt=10
            ... ).delete()
            >>> # Delete all (use with caution)
            >>> count = await User.objects.all().delete()

        Note:
            Performance: O(1) database query regardless of number of matching rows.
            If observers are attached, RETURNING fetches primary keys for notification.
            The deletion remains reversible until the caller commits. Foreign key constraints may prevent
            deletion - handle DatabaseError for constraint violations.
        """
        self._validate_mutation_selection()
        logger.debug(f"Executing .delete() on {self._model.__name__}")
        dialect = self._session.get_bind(mapper=self._model).dialect
        await self._trigger_observers(
            self._observers,
            "pre_bulk_delete",
            queryset=self.queryset,
            model=self._model,
            session=self._session,
        )
        try:
            pk_column = getattr(self._model, self._pk_field)
            compiled = self._compiler.compile(self.query_state)
            stmt: Any = sql_delete(self._model)
            if compiled.whereclause is not None:
                stmt = stmt.where(compiled.whereclause)

            dialect = self._session.get_bind(mapper=self._model).dialect
            if self._observers and dialect.name == "mysql":
                # MySQL 8 does not support DELETE .. RETURNING. Lock matching
                # rows in the caller's transaction to keep the observed PKs
                # stable until DELETE. Relationship-filtered/multi-table deletes
                # cannot safely use this simple fallback.
                from sqlalchemy import select as sql_select

                keys = sql_select(pk_column)
                if compiled.whereclause is not None:
                    keys = keys.where(compiled.whereclause)
                keys = keys.with_for_update()
                locked = await self._session.execute(keys)
                deleted_pks: list[Any] = list(locked.scalars().all())
                result = await self._session.execute(stmt)
                deleted_count = cast(CursorResult[Any], result).rowcount
            else:
                if self._observers:
                    stmt = stmt.returning(pk_column)
                result = await self._session.execute(stmt)
                deleted_pks = list(result.scalars().all()) if self._observers else []
                deleted_count = (
                    len(deleted_pks)
                    if self._observers
                    else cast(CursorResult[Any], result).rowcount
                )
        except Exception as exc:
            logger.error("Delete failed")
            raise DatabaseError(
                message=f"Error deleting {self._model.__name__}", cause=exc
            ) from exc

        if self._observers:
            await self._trigger_observers(
                self._observers,
                "post_bulk_delete",
                doc_ids=deleted_pks,
                queryset=self.queryset,
                model=self._model,
                session=self._session,
                count=deleted_count,
            )
        logger.debug(
            f"Deleted {deleted_count} {self._model.__name__} instance(s) from database"
        )
        return deleted_count
