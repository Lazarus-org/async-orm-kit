from sqlalchemy import String

from async_orm_kit.models.fields.typed.base import BaseValidatedType, ValidatorType
from async_orm_kit.models.fields.validators import (
    MaxLengthValidator,
    MinLengthValidator,
)


class StringField(BaseValidatedType):
    cache_ok = True
    impl = String

    def __init__(
        self,
        max_length: int | None = None,
        min_length: int | None = None,
        validators: list[ValidatorType] | None = None,
        **kwargs,
    ):
        self.min_length = min_length
        self.max_length = max_length

        v: list[ValidatorType] = list(validators or [])

        if max_length is not None:
            v.append(MaxLengthValidator(max_length))

        if min_length is not None:
            v.append(MinLengthValidator(min_length))

        super().__init__(validators=v, **kwargs)

    def load_dialect_impl(self, dialect):
        """Build the dialect string type with the configured maximum length."""
        if self.max_length:
            return dialect.type_descriptor(String(self.max_length))
        return dialect.type_descriptor(String())
