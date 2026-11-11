from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from async_orm_kit.models import Model


class BaseModelObserver:
    """Base observer for single model instance lifecycle"""

    async def pre_save(self, instance: "Model", **kwargs) -> None:
        pass

    async def post_save(self, instance: "Model", created: bool, **kwargs) -> None:
        pass

    async def pre_delete(self, instance: "Model", **kwargs) -> None:
        pass

    async def post_delete(self, instance: "Model", **kwargs) -> None:
        pass
