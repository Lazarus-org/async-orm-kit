from __future__ import annotations

from collections.abc import Callable
from copy import copy
from typing import TYPE_CHECKING, Any, Generic, cast

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from async_orm_kit._logging import get_logger
from async_orm_kit.config import ORMConfig
from async_orm_kit.exceptions import DatabaseError, ObjectDoesNotExist
from async_orm_kit.queryset.queryset import QuerySet
from async_orm_kit.types import QS, ModelType

if TYPE_CHECKING:
    from async_orm_kit.observers.queryset_observer import BaseQuerySetObserver


logger = get_logger(__name__)


class Manager(Generic[QS]):
    """Manager for database queries with advanced features.Provides a high-level interface for querying models.
    Supports custom queryset classes, query hooks, observers, and bulk operations
    optimized for high-scale applications.

    The manager acts as the primary interface between your code and the database,
    providing methods that return QuerySet instances for further filtering and
    manipulation.

    Type Parameters:
        QS: Custom QuerySet type for this manager. Defaults to base QuerySet.

    Features:
        - Custom queryset classes for specialized query behavior
        - Query hooks for automatic filtering (e.g., tenant isolation)
        - Observer pattern for query lifecycle events
        - Bulk operations with batching support
        - Session binding via ``using()`` for cleaner chaining
        - Query introspection (SQL generation, EXPLAIN plans)

    Args:
        queryset_class: Custom QuerySet subclass to use. If None, uses base QuerySet.
        use_cache: Enable query result caching (currently unused, reserved for future).
        cache_ttl: Cache time-to-live in seconds (currently unused).
        config: Optional immutable query-planner configuration for generated QuerySets.

    Example:
        >>> # Basic usage
        >>> class User(Model):
        ...     objects = Manager()  # Default manager
        ...     active = ActiveUserManager()  # Custom manager
        >>>
        >>> # Query via manager
        >>> users = await User.objects.filter(session, username="john")
        >>> active_users = await User.active.all(session)
        >>>
        >>> # Custom manager with default filtering
        >>> class ActiveUserManager(Manager):
        ...     def get_queryset(self, session):
        ...         return super().get_queryset(session).filter(is_active=True)
        >>>
        >>> # Session binding for cleaner syntax
        >>> async with get_session() as session:
        ...     users = await User.objects.using(session).filter(active=True)

    Note:
        Managers are class-level attributes and cannot be accessed via model
        instances. Attempting ``user.objects`` raises AttributeError.
    """

    def __init__(
        self,
        queryset_class: type[QS] | None = None,
        use_cache: bool = False,
        cache_ttl: int = 300,
        config: ORMConfig | None = None,
    ):
        self._model: type[Any] | None = None
        self._queryset_class = queryset_class
        self._observers: list[BaseQuerySetObserver] = []
        self._use_cache = use_cache
        self._cache_ttl = cache_ttl
        self._config = config
        self._query_hooks: list[Callable] = []
        self._result_processors: list[Callable] = []

    def __get__(self, instance, owner):
        """Expose managers on model classes, never on model instances.

        Manager instances are bound by ``ModelMeta``. The descriptor deliberately
        does not mutate its binding during attribute access: doing so makes a
        manager inherited by multiple models point at whichever model was accessed
        most recently.
        """
        if instance is not None:
            raise AttributeError("Manager is not accessible via model instances")
        return self

    def __set_name__(self, owner, name):
        """Descriptor protocol - capture manager name and model class."""
        self._model = owner
        self._manager_name = name

    def clone_for_model(self) -> Manager[QS]:
        """Return an independently bound copy for an inheriting model.

        Custom manager subclasses retain their type and custom attributes. The
        manager-owned mutable registries are copied so changes made on a child
        model do not leak into its parent or sibling models. Custom subclasses
        with additional mutable state should override this method and copy that
        state explicitly.
        """
        clone = copy(self)
        clone._model = None
        clone._observers = list(self._observers)
        clone._query_hooks = list(self._query_hooks)
        clone._result_processors = list(self._result_processors)
        return clone

    def contribute_to_class(self, model: type[ModelType], name: str):
        """Model registration called by ModelMeta.

        Binds this manager to a model class and sets it as a class attribute.
        Called automatically during model class creation by the metaclass.

        Args:
            model: The model class this manager belongs to.
            name: The attribute name for this manager on the model class.

        Example:
            >>> # Called automatically by metaclass
            >>> manager = Manager()
            >>> manager.contribute_to_class(User, "objects")
            >>> User.objects  # Now accessible<Manager for User>
        """
        self._model = model
        self._manager_name = name
        setattr(model, name, self)

    def get_queryset(self, session: AsyncSession) -> QS:
        """Return base queryset for this manager.

        Override in subclasses to provide default filtering, ordering, or other
        queryset modifications that should apply to all queries from this manager.

        Args:
            session: Active database session for query execution.

        Returns:
            QuerySet instance (or custom subclass) bound to this manager's model.

        Example:
            >>> class ActiveManager(Manager):
            ...     def get_queryset(self, session):
            ...         return super().get_queryset(session).filter(is_active=True)>>>
            >>> class User(Model):
            ...     objects = Manager()
            ...     active = ActiveManager()
            >>>
            >>> # active manager automatically filters
            >>> await User.active.all(session)  # Only returns active users
        """
        if self._model is None:
            raise ValueError("Manager is not bound to a model")
        qs_class = self._queryset_class or QuerySet
        queryset_kwargs: dict[str, Any] = {"observers": self._observers}
        if self._config is not None:
            queryset_kwargs["config"] = self._config
        qs = qs_class(self._model, session, **queryset_kwargs)

        # Apply query hooks
        for hook in self._query_hooks:
            qs = hook(qs)

        return cast(QS, qs)

    def add_observer(self, observer: BaseQuerySetObserver) -> Manager:
        """Attach lifecycle observer to all querysets from this manager.

        Observers receive notifications for query lifecycle events like
        pre-execution, post-execution, and errors.

        Args:
            observer: QuerySetObserver instance to register.

        Returns:
            Self for method chaining.

        Example:
            >>> from repository.queryset.observers import LoggingObserver
            >>> User.objects.add_observer(LoggingObserver())
        """
        self._observers.append(observer)
        return self

    def add_query_hook(self, hook: Callable[[QS], QS]) -> Manager:
        """Add hook that modifies every queryset from this manager.

        Query hooks are functions that receive a queryset and return a modified
        queryset. Useful for automatic filtering like tenant isolation or
        soft-delete filtering.

        Args:
            hook: Callable that takes a QuerySet and returns a modified QuerySet.

        Returns:
            Self for method chaining.

        Example:
            >>> def add_tenant_filter(qs):
            ...     return qs.filter(tenant_id=current_tenant_id())
            >>>
            >>> User.objects.add_query_hook(add_tenant_filter)
            >>> # All queries now automatically filter by tenant
            >>> await User.objects.all(session)  # Includes tenant filter
        """
        self._query_hooks.append(hook)
        return self

    def add_result_processor(self, processor: Callable) -> Manager:
        """Reject the legacy placeholder rather than silently ignoring callbacks.

        Use QuerySet observers for execution events or transform fetched results
        explicitly. No result-processor execution contract is implemented.
        """
        raise NotImplementedError(
            "Manager result processors are not implemented; use QuerySet observers "
            "or transform fetched results explicitly."
        )

    def using(self, session: AsyncSession) -> ManagerProxy:
        """Bind session for chaining without passing session to each method.

        Returns a proxy that pre-binds the session to all manager methods,
        enabling cleaner query syntax without repetitive session passing.

        Args:
            session: Active database session to bind.

        Returns:
            ManagerProxy with session pre-bound to all methods.

        Example:
            >>> async with get_session() as session:
            ...     # Without using()
            ...     users = await User.objects.filter(session, active=True)
            ...
            ...     # With using() - cleaner syntax
            ...     users = await User.objects.using(session).filter(active=True)
        """
        return ManagerProxy(self, session)

    # QuerySet proxy methods
    def all(self, session: AsyncSession) -> QuerySet[ModelType]:
        return self.get_queryset(session).all()

    def filter(self, session: AsyncSession, *args, **kwargs) -> QuerySet[ModelType]:
        return self.get_queryset(session).filter(*args, **kwargs)

    def exclude(self, session: AsyncSession, *args, **kwargs) -> QuerySet[ModelType]:
        return self.get_queryset(session).exclude(*args, **kwargs)

    def order_by(self, session: AsyncSession, *fields: str) -> QuerySet[ModelType]:
        return self.get_queryset(session).order_by(*fields)

    def limit(self, session: AsyncSession, limit: int) -> QuerySet[ModelType]:
        return self.get_queryset(session).limit(limit)

    def offset(self, session: AsyncSession, offset: int) -> QuerySet[ModelType]:
        return self.get_queryset(session).offset(offset)

    def select_for_update(
        self, session: AsyncSession, **options
    ) -> QuerySet[ModelType]:
        """Create a row-locking QuerySet within a caller-owned transaction."""
        return self.get_queryset(session).select_for_update(**options)

    def select_related(
        self, session: AsyncSession, *relations: str
    ) -> QuerySet[ModelType]:
        return self.get_queryset(session).select_related(*relations)

    def prefetch_related(
        self, session: AsyncSession, *relations: str
    ) -> QuerySet[ModelType]:
        return self.get_queryset(session).prefetch_related(*relations)

    def annotate(self, session: AsyncSession, **annotations) -> QuerySet[ModelType]:
        return self.get_queryset(session).annotate(**annotations)

    def values(self, session: AsyncSession, *fields: str) -> QuerySet:
        return self.get_queryset(session).values(*fields)

    def values_list(
        self, session: AsyncSession, *fields: str, flat: bool = False
    ) -> QuerySet:
        return self.get_queryset(session).values_list(*fields, flat=flat)

    def distinct(self, session: AsyncSession, *fields: str) -> QuerySet:
        return self.get_queryset(session).distinct(*fields)

    def defer(self, session: AsyncSession, *fields: str) -> QuerySet:
        return self.get_queryset(session).defer(*fields)

    def only(self, session: AsyncSession, *fields: str) -> QuerySet:
        return self.get_queryset(session).only(*fields)

    async def get(self, session: AsyncSession, **kwargs) -> ModelType:
        return await self.get_queryset(session).get(**kwargs)

    async def first(self, session: AsyncSession) -> ModelType | None:
        return await self.get_queryset(session).first()

    async def aggregate(
        self, session: AsyncSession, **aggregates: Any
    ) -> dict[str, Any]:
        """Compute aggregate summaries using the supplied AsyncSession."""
        return await self.get_queryset(session).aggregate(**aggregates)

    async def count(self, session: AsyncSession) -> int:
        return await self.get_queryset(session).count()

    async def exists(self, session: AsyncSession, **kwargs) -> bool:
        if kwargs:
            return await self.get_queryset(session).filter(**kwargs).exists()
        return await self.get_queryset(session).exists()

    async def create(self, session: AsyncSession, **kwargs) -> ModelType:
        return await self.get_queryset(session).create(**kwargs)

    async def bulk_create(
        self,
        session: AsyncSession,
        objects: list[dict],
        *,
        returning: bool = False,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: list[str] | None = None,
        unique_fields: list[str] | None = None,
        batch_size: int | None = None,
    ) -> list | int:
        """
        Bulk create with batching support for very large datasets.

        Args:
            batch_size: Split into batches of this size (default: no batching)
        """
        if batch_size is not None and (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size < 1
        ):
            raise ValueError("batch_size must be a positive integer")

        if batch_size is not None and len(objects) > batch_size:
            results: list[Any] = []
            for i in range(0, len(objects), batch_size):
                batch = objects[i : i + batch_size]
                result = await self.get_queryset(session).bulk_create(
                    batch,
                    returning=returning,
                    ignore_conflicts=ignore_conflicts,
                    update_conflicts=update_conflicts,
                    update_fields=update_fields,
                    unique_fields=unique_fields,
                )
                if returning:
                    results.extend(cast(list, result))
                else:
                    results.append(result)
            return results if returning else sum(results)

        return await self.get_queryset(session).bulk_create(
            objects,
            returning=returning,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    async def get_or_create(
        self, session: AsyncSession, defaults: dict[str, Any] | None = None, **kwargs
    ) -> tuple[ModelType, bool]:
        """
        Get existing object or create new one.

        Returns:
            (instance, created) tuple
        """
        try:
            instance: Any = await self.get(session, **kwargs)
            return instance, False
        except ObjectDoesNotExist:
            create_kwargs = {**kwargs, **(defaults or {})}
            instance = await self.create(session, **create_kwargs)
            return instance, True

    async def update_or_create(
        self, session: AsyncSession, defaults: dict[str, Any] | None = None, **kwargs
    ) -> tuple[ModelType, bool]:
        """
        Update existing object or create new one.

        Returns:
            (instance, created) tuple
        """
        defaults = defaults or {}
        try:
            instance: Any = await self.get(session, **kwargs)
        except ObjectDoesNotExist:
            create_kwargs = {**kwargs, **defaults}
            instance = await self.create(session, **create_kwargs)
            return instance, True

        for key, value in defaults.items():
            setattr(instance, key, value)
        if defaults:
            await instance.save(session, update_fields=list(defaults))
        return instance, False

    async def bulk_update(
        self,
        session: AsyncSession,
        objects: list[ModelType],
        fields: list[str],
        batch_size: int | None = None,
    ) -> int:
        """Update mapped fields on a collection of existing model instances.

        Each batch is flushed once. The supplied session's transaction remains
        caller-owned. Model validation and lifecycle observers run for every
        instance; ``fields`` limits persisted column attributes.
        """
        if not objects:
            return 0
        if not fields:
            raise ValueError("fields must contain at least one mapped column name")
        if batch_size is not None and (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size < 1
        ):
            raise ValueError("batch_size must be a positive integer")

        size = batch_size or len(objects)
        for offset in range(0, len(objects), size):
            batch = objects[offset : offset + size]

            for instance in batch:
                if not isinstance(instance, self._model or ()):
                    raise TypeError(
                        f"bulk_update expected {getattr(self._model, '__name__', 'unbound model')} instances, "
                        f"got {type(instance).__name__}"
                    )
                state = sa_inspect(instance)
                if state.transient or state.pending or state.deleted:
                    raise ValueError("bulk_update requires persisted model instances")

                selected_fields = instance._validate_update_fields(
                    fields, is_insert=False
                )
                try:
                    await instance._prepare_update_fields(
                        session, selected_fields or set()
                    )
                except SQLAlchemyError as exc:
                    raise DatabaseError(
                        message=f"Failed to prepare {getattr(self._model, '__name__', 'unbound model')} bulk update",
                        cause=exc,
                    ) from exc
                await instance.full_clean(update_fields=list(selected_fields or ()))
                await instance._trigger_model_observers(
                    "pre_save",
                    session=session,
                    created=False,
                    update_fields=list(selected_fields or ()),
                )

            try:
                await session.flush()
            except SQLAlchemyError as exc:
                raise DatabaseError(
                    message=f"Failed to bulk update {getattr(self._model, '__name__', 'unbound model')}",
                    cause=exc,
                ) from exc

            for instance in batch:
                await instance._trigger_model_observers(
                    "post_save",
                    session=session,
                    created=False,
                    update_fields=list(fields),
                )

        return len(objects)

    def sql(self, session: AsyncSession, dialect=None) -> str:
        """Get SQL for current queryset"""
        return self.get_queryset(session).sql(dialect=dialect)

    async def explain(
        self, session: AsyncSession, analyze: bool = False, verbose: bool = False
    ) -> str:
        """Get query execution plan"""
        return await self.get_queryset(session).explain(
            analyze=analyze, verbose=verbose
        )

    async def in_bulk(
        self, session: AsyncSession, id_list: list[Any], field_name: str = "id"
    ) -> dict[Any, ModelType]:
        """
        Return dict mapping field values to objects.

        Usage:
            users = await User.objects.in_bulk(session, [1, 2, 3])
            # {1: <User id=1>, 2: <User id=2>, 3: <User id=3>}
        """
        if not id_list:
            return {}

        objects = await self.filter(session, **{f"{field_name}__in": id_list})
        return {getattr(obj, field_name): obj for obj in objects}

    async def earliest(self, session: AsyncSession, *fields: str) -> ModelType | None:
        """Get earliest object by field(s)"""
        if not fields:
            fields = ("id",)
        return await self.get_queryset(session).order_by(*fields).first()

    async def latest(self, session: AsyncSession, *fields: str) -> ModelType | None:
        """Get latest object by field(s)"""
        if not fields:
            fields = ("id",)
        reversed_fields = tuple(
            field[1:] if field.startswith("-") else f"-{field}" for field in fields
        )
        return await self.get_queryset(session).order_by(*reversed_fields).first()


class ManagerProxy:
    """
    Proxy that binds a session to a manager for cleaner chaining.

    Usage:
        async with get_session() as session:
            users = await User.objects.using(session).filter(active=True)
    """

    def __init__(self, manager: Manager, session: AsyncSession):
        self._manager = manager
        self._session = session

    def __getattr__(self, name: str):
        """Proxy all manager methods with session pre-bound"""
        attr = getattr(self._manager, name)
        if callable(attr):

            def wrapper(*args, **kwargs):
                return attr(self._session, *args, **kwargs)

            return wrapper
        return attr
