"""Optional FastAPI integration for request-scoped SQLAlchemy sessions.

FastAPI is imported lazily by the exception-handler registration function.
Importing this module does not require the optional FastAPI package.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from async_orm_kit._logging import get_logger
from async_orm_kit.exceptions import (
    DatabaseError,
    MultipleObjectsReturned,
    ObjectDoesNotExist,
)

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]


def create_session_dependency(
    session_factory: SessionFactory,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    """Create a FastAPI yield dependency for one request-scoped AsyncSession.

    The dependency closes the session after the request and rolls back an
    active transaction when request execution raises or is cancelled. It does
    not commit successful requests: transaction ownership remains explicit and
    belongs to the application/service layer.

    A SQLAlchemy AsyncSession is not safe for concurrent use by multiple tasks.
    The dependency creates a fresh session for every request; do not pass the
    yielded session into concurrently running tasks.

    Args:
        session_factory: A callable such as SQLAlchemy's async_sessionmaker.

    Returns:
        An async-generator dependency suitable for ``Depends(...)``.

    Raises:
        TypeError: If ``session_factory`` is not callable.
    """
    if not callable(session_factory):
        raise TypeError("session_factory must be callable")

    async def dependency() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except BaseException:
                # Roll back only the transaction owned by this session. Preserve
                # the original exception/cancellation if rollback itself fails.
                if session.in_transaction():
                    try:
                        await session.rollback()
                    except Exception:
                        # Session context exit still closes the connection. The
                        # original request failure is more useful to propagate.
                        logger.exception("Request session rollback failed")
                raise

    dependency.__name__ = "get_async_orm_session"
    dependency.__qualname__ = "get_async_orm_session"
    return dependency


def register_exception_handlers(app: Any) -> Any:
    """Register safe HTTP mappings for common Async ORM Kit exceptions.

    Defaults:
        * ``ObjectDoesNotExist`` -> HTTP 404.
        * ``MultipleObjectsReturned`` -> HTTP 409.
        * ``DatabaseError`` -> HTTP 500 with no database details disclosed.

    Query construction errors are deliberately not translated globally because
    they may indicate programming errors rather than invalid client input.
    Applications can register more specific handlers for their own API contract.

    Args:
        app: A FastAPI application instance.

    Returns:
        The same application instance, allowing setup chaining.

    Raises:
        ImportError: If the optional FastAPI dependency is not installed.
        TypeError: If ``app`` does not expose ``add_exception_handler``.
    """
    try:
        from fastapi.responses import JSONResponse
    except ImportError as exc:
        raise ImportError(
            "FastAPI integration requires the optional dependency. "
            "Install async-orm-kit[fastapi]."
        ) from exc

    add_handler = getattr(app, "add_exception_handler", None)
    if not callable(add_handler):
        raise TypeError("app must be a FastAPI-compatible application")

    async def object_not_found(request: Any, exc: ObjectDoesNotExist) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": "Resource not found"},
        )

    async def multiple_objects(
        request: Any, exc: MultipleObjectsReturned
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "Multiple resources matched the request"},
        )

    async def database_failure(request: Any, exc: DatabaseError) -> JSONResponse:
        # Do not return exc.message, exc.cause, SQL, parameters, or credentials.
        return JSONResponse(
            status_code=500,
            content={"detail": "A database operation failed"},
        )

    add_handler(ObjectDoesNotExist, object_not_found)
    add_handler(MultipleObjectsReturned, multiple_objects)
    add_handler(DatabaseError, database_failure)
    return app


__all__ = ["create_session_dependency", "register_exception_handlers"]
