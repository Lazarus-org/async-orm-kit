from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from async_orm_kit.queryset.queryset import QuerySet


class BaseQuerySetObserver:
    """Base observer for queryset operations"""

    async def pre_stream(self, queryset: "QuerySet", **kwargs) -> None:
        pass

    async def post_stream(self, queryset: "QuerySet", **kwargs) -> None:
        pass

    async def pre_execute(self, queryset: "QuerySet", **kwargs) -> None:
        pass

    async def post_execute(self, queryset: "QuerySet", **kwargs) -> None:
        pass

    async def pre_bulk_create(self, **kwargs) -> None:
        pass

    async def post_bulk_create(self, **kwargs) -> None:
        pass

    async def pre_bulk_update(self, queryset: "QuerySet", **kwargs) -> None:
        pass

    async def post_bulk_update(self, queryset: "QuerySet", **kwargs) -> None:
        pass

    async def pre_bulk_delete(self, queryset: "QuerySet", **kwargs) -> None:
        pass

    async def post_bulk_delete(self, queryset: "QuerySet", **kwargs) -> None:
        pass
