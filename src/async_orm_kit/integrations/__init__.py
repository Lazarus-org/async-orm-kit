"""Optional integrations for third-party async libraries.

Importing this package does not import the optional Alembic or FastAPI packages.
"""

from .alembic import get_target_metadata
from .fastapi import create_session_dependency, register_exception_handlers
from .storage import AsyncStorageKitAdapter

__all__ = [
    "AsyncStorageKitAdapter",
    "create_session_dependency",
    "get_target_metadata",
    "register_exception_handlers",
]
