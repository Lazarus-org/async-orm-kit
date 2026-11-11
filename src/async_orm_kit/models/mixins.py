from typing import ClassVar

from async_orm_kit.observers import BaseModelObserver, BaseQuerySetObserver
from async_orm_kit.observers.mixin import ObserverMixin


class ModelObserverMixin(ObserverMixin):
    """Mixin to add observer support to Model"""

    model_observers: ClassVar[list[BaseModelObserver]] = []
    _model_observers: ClassVar[list[BaseModelObserver]] = []
    queryset_observers: ClassVar[list[BaseQuerySetObserver]] = []
    _queryset_observers: ClassVar[list[BaseQuerySetObserver]] = []

    async def _trigger_model_observers(self, event: str, **kwargs) -> None:
        """Trigger model-level observers"""
        await self._trigger_observers(
            self._model_observers, event, instance=self, **kwargs
        )
