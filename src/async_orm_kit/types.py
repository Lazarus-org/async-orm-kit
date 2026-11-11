from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from async_orm_kit.models import Model
    from async_orm_kit.queryset import QuerySet

ModelType = TypeVar("ModelType", bound="Model")
QS = TypeVar("QS", bound="QuerySet")

SAFE_SCALAR_TYPES = (
    str,
    int,
    float,
    bool,
    type(None),
    Decimal,
    date,
    datetime,
    time,
    timedelta,
)
