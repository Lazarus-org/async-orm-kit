import re
from typing import Any

from async_orm_kit.models.fields.exceptions import FieldValidationError


class FieldValidator:
    def __call__(self, value: Any) -> None:
        """
        Base validator interface.

        Contract:
          - Do nothing if `value` is valid.
          - Raise FieldValidationError (or subclass) if invalid.
          - Does NOT return or transform the value.
        """
        return None


class MaxLengthValidator(FieldValidator):
    def __init__(self, limit: int):
        self.limit = limit

    def __call__(self, value) -> None:
        if value is None:
            return
        try:
            length = len(value)
        except TypeError:
            raise FieldValidationError(
                f"Ensure this value has at most {self.limit} characters "
                f"(it has a non-measurable length).",
                code="max_length",
            ) from None
        if length > self.limit:
            raise FieldValidationError(
                f"Ensure this value has at most {self.limit} characters "
                f"(it has {length}).",
                code="max_length",
            )


class MinLengthValidator(FieldValidator):
    def __init__(self, limit: int):
        self.limit = limit

    def __call__(self, value) -> None:
        if value is None:
            return
        try:
            length = len(value)
        except TypeError:
            raise FieldValidationError(
                f"Ensure this value has at least {self.limit} characters "
                f"(it has a non-measurable length).",
                code="min_length",
            ) from None
        if length < self.limit:
            raise FieldValidationError(
                f"Ensure this value has at least {self.limit} characters "
                f"(it has {length}).",
                code="min_length",
            )


class RangeValidator(FieldValidator):
    def __init__(self, *, min: int | None = None, max: int | None = None):
        self.min = min
        self.max = max

    def __call__(self, value) -> None:
        if value is None:
            return

        # Non-numeric or non-orderable values
        try:
            _ = value < value  # just to trigger TypeError for non-comparables
        except TypeError:
            raise FieldValidationError(
                "Ensure this value is a comparable numeric-type.",
                code="invalid_type",
            ) from None

        if self.min is not None and value < self.min:
            raise FieldValidationError(
                f"Ensure this value is greater than or equal to {self.min} "
                f"(it is {value}).",
                code="min_value",
            )
        if self.max is not None and value > self.max:
            raise FieldValidationError(
                f"Ensure this value is less than or equal to {self.max} "
                f"(it is {value}).",
                code="max_value",
            )


class Regex(FieldValidator):
    def __init__(self, pattern: str | re.Pattern, message: str = "Invalid format"):
        self.pattern = re.compile(pattern) if isinstance(pattern, str) else pattern
        self.message = message

    def __call__(self, value) -> None:
        if value is None:
            return
        if not self.pattern.match(value):
            raise FieldValidationError(
                self.message,
                code="regex",
            )
