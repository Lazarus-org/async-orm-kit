from __future__ import annotations

import warnings
from enum import Enum
from typing import Any, ClassVar

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import declared_attr

from async_orm_kit._logging import get_logger
from async_orm_kit.exceptions import DatabaseError
from async_orm_kit.manager import Manager
from async_orm_kit.models.meta import Base, ModelMeta
from async_orm_kit.models.mixins import ModelObserverMixin
from async_orm_kit.types import ModelType

logger = get_logger(__name__)


class Model(ModelObserverMixin, Base, metaclass=ModelMeta):
    """Base model with built-in manager support.

    Provides a familiar Django-like interface for SQLAlchemy models with
    automatic manager registration, serialization helpers, validation hooks,
    and observer pattern support for lifecycle events.

    Features:
        - Automatic manager registration and discovery
        - Auto-generated table names from class names
        - Dictionary serialization with relationship traversal
        - Validation hooks via ``clean()`` method
        - Observer pattern for pre/post save/delete events
        - Partial field updates via ``update_fields``
        - Model introspection utilities

    Class Attributes:
        _managers: List of manager attribute names registered on this model.
        _default_manager: The default manager instance (typically ``objects``).

    Example:
        >>> class User(Model):
        ...     __tablename__ = "users"
        ...
        ...     id: Mapped[int] = mapped_column(primary_key=True)
        ...     username: Mapped[str]
        ...     email: Mapped[str]
        ...
        ...     # Custom manager
        ...     active = ActiveUserManager()...
        >>> # Query via manager
        >>> users = await User.objects.filter(session, username="john")
        >>>
        >>> # Save with partial update
        >>> user.email = "new@example.com"
        >>> await user.save(session, update_fields=["email"])>>>
        >>> # Serialize to dict
        >>> data = user.to_dict(exclude={"password"})

    Note:
        All models must define ``__tablename__`` or rely on the auto-generated
        name from ``__tablename__`` declared_attr. Models are abstract by
        default via ``__abstract__ = True``.
    """

    __abstract__ = True
    _model_field_types: ClassVar[dict[str, Any]]

    # Class-level manager tracking
    _managers: ClassVar[list[str]] = []
    _default_manager: ClassVar[Manager | None] = None

    @declared_attr.directive
    def __tablename__(cls) -> str:  # pylint: disable=no-self-argument  # SQLAlchemy class-level declared attribute.
        """Auto-generate table name from class name using snake_case pluralization.

        Converts CamelCase class names to snake_case and applies basic English
        pluralization rules. Override in subclass for custom table names.

        Pluralization Rules:
            - Words ending in consonant + 'y' → 'ies' (Category → categories)
            - Words ending in s, x, z, ch, sh → add 'es' (Status → statuses)
            - All others → add 's' (User → users)

        Returns:
            Pluralized snake_case table name derived from class name.

        Example:
            >>> class UserProfile(Model): pass
            >>> UserProfile.__tablename__
            'user_profiles'>>>
            >>> class Order(Model): pass
            >>> Order.__tablename__
            'orders'
            >>>
            >>> class Category(Model): pass
            >>> Category.__tablename__
            'categories'
        """
        import re

        # CamelCase -> snake_case
        name = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()

        # Pluralization rules
        if len(name) > 1 and name.endswith("y") and name[-2] not in "aeiou":
            return name[:-1] + "ies"
        if name.endswith(("s", "x", "z", "ch", "sh")):
            return name + "es"

        return name + "s"

    def __repr__(self) -> str:
        """Enhanced repr showing primary key values.

        Returns:
            String representation in format ``<ClassName(pk1=val1, pk2=val2)>``.

        Example:
            >>> user = User(id=1, username="john")
            >>> repr(user)
            '<User(id=1)>'
        """
        mapper = sa_inspect(self.__class__)
        pk_values = [
            f"{col.name}={getattr(self, mapper.get_property_by_column(col).key, None)}"
            for col in mapper.primary_key
        ]
        return f"<{self.__class__.__name__}({', '.join(pk_values)})>"

    def __str__(self) -> str:
        """String representation - override in subclasses"""
        return self.__repr__()

    @classmethod
    def get_manager(cls, name: str = "objects") -> Manager:
        """Get a specific manager by name.

        Args:
            name: Manager attribute name. Defaults to "objects".

        Returns:
            The requested Manager instance.

        Raises:
            AttributeError: If the manager doesn't exist or isn't a Manager instance.

        Example:
            >>> User.get_manager("active")
            <ActiveUserManager for User>
        """
        manager = getattr(cls, name, None)
        if not isinstance(manager, Manager):
            raise AttributeError(f"Manager '{name}' not found on {cls.__name__}")
        return manager

    @classmethod
    def get_default_manager(cls) -> Manager:
        """Get the default manager (usually 'objects').

        Returns:
            The default Manager instance, falling back to ``objects`` if
            ``_default_manager`` is not set.

        Example:
            >>> User.get_default_manager()
            <Manager for User>
        """
        return cls._default_manager or cls.get_manager("objects")

    @classmethod
    def get_all_managers(cls) -> dict[str, Manager]:
        """Get all managers defined on this model.

        Returns:
            Dictionary mapping manager names to Manager instances.

        Example:
            >>> User.get_all_managers()
            {'objects': <Manager for User>, 'active': <ActiveUserManager for User>}
        """
        return {name: getattr(cls, name) for name in cls._managers}

    def to_dict(
        self,
        exclude: set[str] | None = None,
        include: set[str] | None = None,
        follow_relations: bool = False,
        enum_to_value: bool = False,
    ) -> dict[str, Any]:
        """
        Convert model instance to dictionary.

        Args:
            exclude: Fields to exclude
            include: Only include these fields (overrides exclude)
            follow_relations: Include related objects (one level deep)
            enum_to_value: Convert enum objects to their string values
        """
        exclude = exclude or set()
        mapper = sa_inspect(self.__class__)

        result = {}

        # Get column attributes
        for column in mapper.columns:
            if include and column.name not in include:
                continue
            if column.name in exclude:
                continue

            value = getattr(self, mapper.get_property_by_column(column).key, None)
            if enum_to_value and isinstance(value, Enum):
                value = value.value
            result[column.name] = value

        # Optionally include relationships
        if follow_relations:
            for relationship in mapper.relationships:
                if include and relationship.key not in include:
                    continue
                if relationship.key in exclude:
                    continue

                related = getattr(self, relationship.key, None)
                if related is None:
                    result[relationship.key] = None
                elif isinstance(related, list):
                    result[relationship.key] = [
                        (
                            obj.to_dict(follow_relations=False)
                            if hasattr(obj, "to_dict")
                            else str(obj)
                        )
                        for obj in related
                    ]
                else:
                    result[relationship.key] = (
                        related.to_dict(follow_relations=False)
                        if hasattr(related, "to_dict")
                        else str(related)
                    )

        return result

    @classmethod
    def from_dict(cls: type[ModelType], data: dict[str, Any]) -> ModelType:
        """
        Create model instance from dictionary.

        Usage:
            user = User.from_dict({"username": "john", "email": "john@example.com"})
        """
        mapper = sa_inspect(cls)
        attribute_by_column = {
            col.name: mapper.get_property_by_column(col).key for col in mapper.columns
        }

        # Filter to only valid columns
        filtered_data = {
            attribute_by_column[k]: v
            for k, v in data.items()
            if k in attribute_by_column
        }

        return cls(**filtered_data)

    def update_from_dict(self, data: dict[str, Any], commit: bool = False) -> Model:
        """Assign mapped column values from a dictionary without database I/O.

        The instance is only marked dirty in memory. Persist it with ``save(session)``
        or let the application flush it through its SQLAlchemy session.

        ``commit`` is retained temporarily for source compatibility, but is ignored.
        Passing ``True`` emits a deprecation warning because this synchronous helper
        has no session and never performs database I/O.
        """
        if commit:
            warnings.warn(
                "update_from_dict(commit=True) is deprecated and has no effect; "
                "commit through the application-owned SQLAlchemy session",
                DeprecationWarning,
                stacklevel=2,
            )

        mapper = sa_inspect(self.__class__)
        attribute_by_column = {
            col.name: mapper.get_property_by_column(col).key for col in mapper.columns
        }

        for key, value in data.items():
            if key in attribute_by_column:
                setattr(self, attribute_by_column[key], value)

        return self

    @classmethod
    def get_field_names(cls, include_relationships: bool = False) -> list[str]:
        """Get list of all field names"""
        mapper = sa_inspect(cls)
        fields = [col.name for col in mapper.columns]

        if include_relationships:
            fields.extend([rel.key for rel in mapper.relationships])

        return fields

    @classmethod
    def get_primary_keys(cls) -> list:
        """Get primary key column"""
        mapper = sa_inspect(cls)
        return [col.key for col in mapper.primary_key]

    @classmethod
    def get_primary_key_names(cls) -> list[str]:
        """Get primary key column names"""
        mapper = sa_inspect(cls)
        return [col.name for col in mapper.primary_key]

    def get_primary_key_values(self) -> dict[str, Any]:
        """Get primary key values for this instance"""
        mapper = sa_inspect(type(self))
        return {
            col.name: getattr(self, mapper.get_property_by_column(col).key)
            for col in mapper.primary_key
        }

    async def refresh_from_db(self, session: AsyncSession) -> Model:
        """Asynchronously reload this instance from the database.

        Detached instances are attached to the supplied session. Transient or
        pending instances cannot be refreshed. The method does not commit or roll
        back the surrounding transaction.
        """
        state = sa_inspect(self)
        if state.transient or state.pending or state.deleted:
            raise ValueError("Only persistent or detached instances can be refreshed")

        target_session = getattr(session, "sync_session", None)
        if state.session is not None and target_session is not None:
            if state.session is not target_session:
                raise ValueError(
                    "The instance belongs to a different SQLAlchemy session"
                )
        if state.detached:
            session.add(self)

        await session.refresh(self)
        return self

    def _validate_update_fields(
        self,
        update_fields: list[str] | None,
        *,
        is_insert: bool,
    ) -> set[str] | None:
        """Validate and normalize a partial-update field selection."""
        if update_fields is None:
            return None

        selected_fields = set(update_fields)
        mapper = sa_inspect(type(self))
        valid_fields = {attribute.key for attribute in mapper.column_attrs}
        invalid_fields = selected_fields - valid_fields
        if invalid_fields:
            raise ValueError(
                f"Invalid update_fields for {type(self).__name__}: "
                f"{sorted(invalid_fields)}"
            )
        if is_insert and selected_fields:
            raise ValueError("update_fields cannot be used when inserting a new model")
        return selected_fields

    async def _prepare_update_fields(
        self, session: AsyncSession, selected_fields: set[str]
    ) -> None:
        """Attach the instance and discard dirty column changes not selected.

        Relationship mutations are rejected because a column-only partial update
        cannot safely infer whether those changes should be persisted.
        """
        mapper = sa_inspect(type(self))
        state = sa_inspect(self)

        dirty_relationships = [
            relationship.key
            for relationship in mapper.relationships
            if state.attrs[relationship.key].history.has_changes()
        ]
        if dirty_relationships:
            raise ValueError(
                "update_fields cannot be combined with pending relationship "
                f"changes: {', '.join(dirty_relationships)}"
            )

        session.add(self)
        unselected_dirty_columns = [
            attribute.key
            for attribute in mapper.column_attrs
            if attribute.key not in selected_fields
            and state.attrs[attribute.key].history.has_changes()
        ]
        if unselected_dirty_columns:
            # Refreshing one object must not autoflush dirty attributes on other
            # objects participating in the same bulk operation.
            with session.no_autoflush:
                await session.refresh(self, attribute_names=unselected_dirty_columns)

    async def save(
        self,
        session: AsyncSession,
        force_insert: bool = False,
        update_fields: list[str] | None = None,
    ) -> Model:
        """Add or update this model and flush it in the caller's transaction.

        ``save`` never commits or rolls back the supplied session. This is
        intentional: transaction ownership belongs to the application. Use
        ``async with session.begin():`` or explicitly call ``commit``/``rollback``
        around one or more ORM operations. If a flush fails, SQLAlchemy marks the
        transaction as failed; the caller must roll it back before reusing the session.

        ``update_fields`` limits an update to mapped column attributes. Dirty
        non-selected column attributes are refreshed from the database before
        flushing so they are not accidentally persisted. Relationship changes
        must be flushed separately and cannot be combined with ``update_fields``.

        Lifecycle observers are awaited synchronously. ``post_save`` means that
        the flush succeeded; it does not mean the surrounding transaction has
        committed. No background tasks are created.
        """
        state = sa_inspect(self)
        if state.deleted:
            raise ValueError("A deleted model instance cannot be saved")

        is_new = state.transient or state.pending
        if force_insert and not state.transient:
            raise ValueError(
                "force_insert can only be used with a transient model instance"
            )
        is_insert = force_insert or is_new

        selected_fields = self._validate_update_fields(
            update_fields, is_insert=is_insert
        )
        if selected_fields is not None and not selected_fields and not is_insert:
            return self

        if selected_fields is not None and not is_insert:
            try:
                await self._prepare_update_fields(session, selected_fields)
            except SQLAlchemyError as exc:
                raise DatabaseError(
                    message=f"Failed to prepare {type(self).__name__} update",
                    cause=exc,
                ) from exc

        # Validate after partial-update restoration so model-level validation
        # sees the values that will actually be persisted.
        await self.full_clean(
            update_fields=(
                list(selected_fields) if selected_fields is not None else None
            )
        )
        await self._trigger_model_observers(
            "pre_save",
            session=session,
            created=is_insert,
            update_fields=(
                list(selected_fields) if selected_fields is not None else None
            ),
        )

        try:
            if selected_fields is None or is_insert:
                session.add(self)

            await session.flush()
            # Refresh server-generated defaults and trigger-updated values while
            # keeping the transaction open and owned by the caller.
            await session.refresh(self)
        except SQLAlchemyError as exc:
            raise DatabaseError(
                message=f"Failed to save {type(self).__name__}", cause=exc
            ) from exc

        await self._trigger_model_observers(
            "post_save",
            session=session,
            created=is_insert,
            update_fields=(
                list(selected_fields) if selected_fields is not None else None
            ),
        )
        return self

    async def delete(self, session: AsyncSession) -> None:
        """Delete this instance and flush within the caller's transaction.

        ``pre_delete`` and ``post_delete`` observers are awaited in registration
        order. ``post_delete`` runs after a successful flush but before commit.
        The method never commits or rolls back the supplied session.
        """
        await self._trigger_model_observers("pre_delete", session=session)

        try:
            await session.delete(self)
            await session.flush()
        except SQLAlchemyError as exc:
            raise DatabaseError(
                message=f"Failed to delete {type(self).__name__}", cause=exc
            ) from exc

        await self._trigger_model_observers("post_delete", session=session)

    def _is_persisted(self) -> bool:
        """Check if instance exists in database.

        Returns:
            True if instance is persistent or detached (has been saved),
            False if transient (never saved).
        """
        return sa_inspect(self).persistent or sa_inspect(self).detached

    async def clean(self) -> None:
        """Validation hook - override in subclasses.

        Called automatically before save operations. Raise exceptions to prevent
        invalid data from being persisted.

        Raises:
            ValueError, ValidationError, or any exception to abort save.

        Example:
            >>> class User(Model):
            ...     async def clean(self):
            ...         if not self.email:
            ...             raise ValueError("Email is required")
            ...         if "@" not in self.email:
            ...             raise ValueError("Invalid email format")
        """
        pass

    async def full_clean(self, update_fields: list[str] | None = None) -> None:
        """
        Run field-level and model-level validation.

        Args:
            update_fields: If provided, only validate these fields. Otherwise validate all.
        """
        # Determine which fields to validate
        if update_fields:
            fields_to_validate = {
                name: field
                for name, field in self._model_field_types.items()
                if name in update_fields
            }
        else:
            fields_to_validate = self._model_field_types

        # Validate selected fields
        for field_name, field in fields_to_validate.items():
            value = getattr(self, field_name)
            await field.clean(value, self)

        # Run model-level validation
        await self.clean()
