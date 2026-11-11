"""Small structured-context adapter over Python's standard logging package.

The ORM does not require a particular logging framework. Keyword context is
attached to each LogRecord as ``orm_context`` so applications can route it
through their own formatters or structured logging handlers.
"""

from __future__ import annotations

import logging
from typing import Any


class ContextLogger:
    """Logger that accepts structlog-style keyword context without a dependency."""

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(self, level: int, event: object, *args: Any, **kwargs: Any) -> None:
        exc_info = kwargs.pop("exc_info", None)
        stack_info = kwargs.pop("stack_info", False)
        stacklevel = kwargs.pop("stacklevel", 1)
        extra = kwargs.pop("extra", None)
        record_extra = dict(extra or {})
        if kwargs:
            record_extra["orm_context"] = kwargs
        self._logger.log(
            level,
            event,
            *args,
            exc_info=exc_info,
            stack_info=stack_info,
            stacklevel=stacklevel + 1,
            extra=record_extra or None,
        )

    def debug(self, event: object, *args: Any, **kwargs: Any) -> None:
        self._log(logging.DEBUG, event, *args, **kwargs)

    def info(self, event: object, *args: Any, **kwargs: Any) -> None:
        self._log(logging.INFO, event, *args, **kwargs)

    def warning(self, event: object, *args: Any, **kwargs: Any) -> None:
        self._log(logging.WARNING, event, *args, **kwargs)

    def error(self, event: object, *args: Any, **kwargs: Any) -> None:
        self._log(logging.ERROR, event, *args, **kwargs)

    def exception(self, event: object, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("exc_info", True)
        self._log(logging.ERROR, event, *args, **kwargs)


def get_logger(name: str) -> ContextLogger:
    """Return a context-aware logger backed by stdlib logging."""

    return ContextLogger(name)
