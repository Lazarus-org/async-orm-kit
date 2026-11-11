from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any


class ObserverMixin:
    """Shared, deterministic dispatcher for model and QuerySet observers."""

    async def _trigger_observers(
        self, observers: Iterable[Any], event: str, **kwargs: Any
    ) -> None:
        """Invoke observers in registration order.

        Both synchronous and asynchronous handlers are supported. Exceptions are
        intentionally propagated to the caller; observer failures must never be
        hidden in untracked background tasks.
        """
        for observer in observers:
            handler = getattr(observer, event, None)
            if handler is None:
                continue
            result = handler(**kwargs)
            if inspect.isawaitable(result):
                await result
