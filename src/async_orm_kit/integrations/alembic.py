"""Alembic helpers for Async ORM Kit model metadata.

Alembic remains an optional development/migration dependency. This module only
uses SQLAlchemy's public MetaData API and does not import Alembic itself.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

from sqlalchemy import MetaData

from async_orm_kit.models.meta import Base


def get_target_metadata(*model_modules: str | ModuleType) -> MetaData:
    """Import application model modules and return Async ORM Kit metadata.

    Alembic autogenerate can only compare tables that have been registered with
    SQLAlchemy's declarative metadata. Pass the application modules that define
    your models before returning ``Base.metadata``::

        target_metadata = get_target_metadata("myapp.models")

    Modules are imported in the order supplied. Import errors intentionally
    propagate so a broken model import cannot silently produce an incomplete
    migration. Calling this function without arguments returns the metadata for
    models that have already been imported.

    Args:
        *model_modules: Trusted importable module names or already-imported
            module objects containing application model declarations.

    Returns:
        The shared SQLAlchemy MetaData instance used by ``async_orm_kit.Model``.

    Raises:
        TypeError: If a supplied value is neither a module name nor a module.
        ValueError: If a supplied module name is empty or whitespace.
        ImportError: If a named application module cannot be imported.
    """
    for module in model_modules:
        if isinstance(module, str):
            if not module.strip():
                raise ValueError("model module names must not be empty")
            import_module(module)
        elif not isinstance(module, ModuleType):
            raise TypeError(
                "model_modules values must be module names or module objects"
            )

    return Base.metadata


__all__ = ["get_target_metadata"]
