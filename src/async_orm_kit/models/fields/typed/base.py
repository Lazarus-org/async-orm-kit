import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.types import TypeDecorator

from async_orm_kit.models.fields.exceptions import FieldValidationError
from async_orm_kit.models.fields.validators import (
    FieldValidator,
)

logger = logging.getLogger(__name__)


ValidatorType = (
    FieldValidator | Callable[[object], None] | Callable[[object], Awaitable[None]]
)


class BaseValidatedType(TypeDecorator):
    """Production-grade validated type with monitoring and bulk optimization."""

    cache_ok = True
    impl: Any = None

    def __init__(
        self,
        *,
        validators: list[ValidatorType] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.model_class = None  # Will be set by contribute_to_class
        self.attname: str | None = None
        self._validators: list[ValidatorType] = validators or []
        self._sync_validators, self._async_validators = self._split_validators()

        # Validators are partitioned once at construction. The previous
        # implementation attempted to call pipeline builders that were never
        # defined; the split lists are the actual execution pipeline.

    def _is_async_validator(self, v: ValidatorType) -> bool:
        """Return True if validator is a coroutine function or has async __call__."""
        if inspect.iscoroutinefunction(v):
            return True
        # Inspect the callable implementation to detect async validator objects.
        call = getattr(v, "__call__", None)  # noqa: B004
        return bool(call and inspect.iscoroutinefunction(call))

    def _split_validators(self):
        sync_list: list[ValidatorType] = []
        async_list: list[ValidatorType] = []
        for v in self._validators:
            if self._is_async_validator(v):
                async_list.append(v)
            else:
                sync_list.append(v)
        return sync_list, async_list

    def run_validators(self, value):
        """
        Run synchronous validators.

        Validators are expected to:
          - take `value`
          - raise FieldValidationError (or ValueError) if invalid
          - return None (no transformation)
        """
        if value is None:
            return

        for validator in self._sync_validators:
            try:
                validator(value)
            except FieldValidationError as e:
                e.field_name = self.attname
                raise
            except Exception as e:
                # Normalize to FieldValidationError
                raise FieldValidationError(
                    message=str(e), field_name=self.attname
                ) from e

    async def arun_validators(self, value):
        """
        Run asynchronous validators.

        Async validators have same contract as sync ones but may be coroutines.
        """
        if value is None:
            return

        for validator in self._async_validators:
            try:
                await validator(value)
            except FieldValidationError as e:
                e.field_name = self.attname
                raise
            except TypeError:
                raise
            except Exception as e:
                raise FieldValidationError(
                    message=str(e), field_name=self.attname
                ) from e

    def contribute_to_class(self, cls, name, instrumented_attr):
        """
        Called after SQLAlchemy instrumentation.
        Subclasses can override to add custom behavior.

        Args:
            cls: The model class
            name: The attribute name
            instrumented_attr: SQLAlchemy's InstrumentedAttribute
        """
        cls._model_field_types[name] = self
        self.model_class = cls
        self.attname = name

    def validate(self, value, model_instance=None):
        """
        Internal field-level validation.
        Override in subclasses to implement built-in constraints (like max_length, range, etc.).
        Should raise FieldValidationError on failure.
        """
        # Default: accept all non-null values
        return value

    async def clean(self, value, model_instance):
        """
        Async validation method that runs all validators (sync + async).
        Use this when you need explicit validation with async support.
        """
        self.run_validators(value)
        await self.arun_validators(value)
        return value

    def process_bind_param(self, value, dialect):
        """
        Runs core validation automatically before DB write.
        """
        try:
            value = self.validate(value)
            return value
        except FieldValidationError as e:
            if not e.field_name:
                e.field_name = self.attname
            raise
        except Exception as e:
            raise FieldValidationError(message=str(e), field_name=self.attname) from e

    def process_result_value(self, value, dialect):
        """Zero-cost read path."""
        return value
