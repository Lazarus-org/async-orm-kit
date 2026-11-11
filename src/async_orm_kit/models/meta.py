from __future__ import annotations

from typing import Any

from sqlalchemy.orm import DeclarativeBase

from async_orm_kit._logging import get_logger
from async_orm_kit.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from async_orm_kit.manager import Manager
from async_orm_kit.models.fields.typed.base import BaseValidatedType

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models.
    Provides the foundation for all model classes in the framework.
    Models should inherit from ``Model`` rather than using this directly.
    """

    pass


class ModelMeta(type(Base)):  # type: ignore[misc]  # SQLAlchemy supplies the declarative metaclass dynamically.
    """Metaclass for automatic manager registration and model initialization.

    Handles the setup of managers with observers and model-specific exceptions
    during class creation. Automatically discovers managers from the class
    hierarchy, creates model-specific exception classes, and registers observers.

    Responsibilities:
        - Create model-specific ``DoesNotExist`` and ``MultipleObjectsReturned``
          exception classes for each model
        - Discover and register Manager instances with their observers from class and parent classes
        - Add default ``objects`` manager if no managers are defined
        - Collect and register model and queryset observers from class hierarchy
        - Call ``contribute_to_class()`` on each manager to bind it to the model

    Class Attributes Created:
        DoesNotExist: Model-specific exception for missing objects.
        MultipleObjectsReturned: Model-specific exception for ambiguous queries.
        _managers: List of manager attribute names on this model.
        _default_manager: The default manager instance (typically ``objects``).
        _model_observers: List of registered model lifecycle observers.

    Example:
        >>> class User(Model):  # Uses ModelMeta automatically
        ...     id: Mapped[int] = mapped_column(primary_key=True)
        ...     username: Mapped[str]
        ...
        ...     # Custom manager
        ...     active = ActiveUserManager()
        >>>
        >>> # Model-specific exceptions created automatically
        >>> try:
        ...     await User.objects.get(id=999)
        ... except User.DoesNotExist:
        ...     print("User not found")
        >>>
        >>> # Managers registered and accessible
        >>> User._managers
        ['objects', 'active']
        >>> User._default_manager
        <Manager for User>

    Note:
        This metaclass is applied automatically when inheriting from ``Model``.
        Abstract models (``__abstract__ = True``) skip manager registration.
    """

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> type:
        """Create and initialize a new model class.

        Args:
            name: Name of the class being created.
            bases: Tuple of base classes.
            namespace: Class namespace dictionary containing attributes and methods.
            **kwargs: Additional keyword arguments passed to parent metaclass.

        Returns:
            Newly created and initialized model class with managers and
            exceptions registered.
        """
        # Create the class
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)

        # Skip for abstract base classes
        if namespace.get("__abstract__", False):
            return cls

        # Create model-specific exceptions
        cls.DoesNotExist = type(
            f"{name}.DoesNotExist",
            (ObjectDoesNotExist,),
            {"__module__": cls.__module__},
        )

        cls.MultipleObjectsReturned = type(
            f"{name}.MultipleObjectsReturned",
            (MultipleObjectsReturned,),
            {"__module__": cls.__module__},
        )

        cls._model_field_types = {}  # {field_name: field_instance}
        cls._file_fields = {}
        if hasattr(cls, "__table__"):
            for column in cls.__table__.columns:
                if isinstance(column.type, BaseValidatedType):
                    attr_name = column.key
                    attr = getattr(cls, attr_name)
                    column.type.contribute_to_class(cls, attr_name, attr)

        # Collect inherited managers and observers in MRO order. Managers must
        # be cloned for each concrete model because they hold a model binding and
        # mutable hooks/observer registries.
        inherited_managers: dict[str, Manager] = {}
        observers: list[Any] = []
        queryset_observers: list[Any] = []

        def append_unique(target: list[Any], values: Any) -> None:
            for value in values or ():
                if all(existing is not value for existing in target):
                    target.append(value)

        # Resolve manager attributes using normal Python MRO precedence. A
        # nearer non-manager attribute intentionally shadows a base manager.
        seen_attributes: set[str] = set()
        for base in cls.__mro__[1:]:
            for attr_name, attr_value in base.__dict__.items():
                if attr_name in seen_attributes:
                    continue
                seen_attributes.add(attr_name)
                if isinstance(attr_value, Manager):
                    inherited_managers[attr_name] = attr_value

        # Observer registration order is base-to-derived and stable. Identity
        # de-duplication avoids requiring observer instances to be hashable.
        for base in reversed(cls.__mro__[1:]):
            append_unique(observers, base.__dict__.get("model_observers", ()))
            append_unique(
                queryset_observers,
                base.__dict__.get("queryset_observers", ()),
            )

        managers: dict[str, Manager] = {
            name: manager.clone_for_model()
            for name, manager in inherited_managers.items()
        }

        # Explicit managers on this class override inherited managers.
        for attr_name, attr_value in namespace.items():
            if isinstance(attr_value, Manager):
                managers[attr_name] = attr_value

        append_unique(observers, namespace.get("model_observers", ()))
        append_unique(queryset_observers, namespace.get("queryset_observers", ()))

        # Add a fresh default manager when no manager is inherited or declared.
        if not managers:
            managers["objects"] = Manager()

        # Bind every manager to this model and give it an independent observer list.
        for manager_name, manager in managers.items():
            manager._observers = list(manager._observers)
            append_unique(manager._observers, queryset_observers)
            manager.contribute_to_class(cls, manager_name)

        cls._managers = list(managers)
        cls._default_manager = managers.get("objects") or next(
            iter(managers.values()), None
        )
        cls._model_observers = observers
        cls._queryset_observers = queryset_observers

        return cls
