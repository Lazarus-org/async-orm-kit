"""Scalar validation, typed expression, and error contract coverage."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import DateTime, Integer, String, column, literal, select

from async_orm_kit import Abs, Cast, F, Lead, Rank, Value, Window
from async_orm_kit.models.fields.exceptions import FieldValidationError
from async_orm_kit.models.fields.typed.base import BaseValidatedType
from async_orm_kit.models.fields.validators import (
    FieldValidator,
    MaxLengthValidator,
    MinLengthValidator,
    RangeValidator,
    Regex,
)
from async_orm_kit.query.expressions.collections import (
    ArrayContains,
    ArrayIndex,
    ArrayLength,
    JSONContains,
    JSONHasKey,
)
from async_orm_kit.query.expressions.functions import Concat, SQLFunction, _operand
from async_orm_kit.query.expressions.window import Lag, _WindowFunction
from async_orm_kit.query.lookups import (
    LOOKUP_OPERATORS,
    _lookup_date_part,
    _lookup_iexact,
    _lookup_range,
)
from async_orm_kit.query.lookups.resolver import ResolverContext


@pytest.mark.parametrize(
    "validator,valid,invalid",
    [
        (MaxLengthValidator(3), ["abc", "", None], ["abcd", 4]),
        (MinLengthValidator(2), ["ab", "abc", None], ["", 4]),
        (RangeValidator(min=1, max=3), [1, 2, 3, None], [0, 4, object()]),
        (RangeValidator(), [1, None], [object()]),
        (Regex(r"^a", "must start with a"), ["abc", None], ["bad"]),
        (Regex(__import__("re").compile(r"^a")), ["abc"], ["bad"]),
    ],
)
def test_validator_boundaries_and_invalid_types(validator, valid, invalid):
    for value in valid:
        assert validator(value) is None
    for value in invalid:
        with pytest.raises(FieldValidationError):
            validator(value)
    assert FieldValidator()(object()) is None


class ValidatedString(BaseValidatedType):
    impl = String


class AsyncValidator:
    async def __call__(self, value):
        if value == "invalid":
            raise FieldValidationError("invalid")
        if value == "type-error":
            raise TypeError("validator type failure")


@pytest.mark.asyncio
async def test_validator_pipelines_normalize_and_preserve_failures():
    async def fail(value):
        raise RuntimeError("async failure")

    def sync_fail(value):
        raise RuntimeError("sync failure")

    field = ValidatedString(validators=[AsyncValidator()])
    field.attname = "label"
    assert field.run_validators(None) is None
    assert await field.arun_validators(None) is None
    assert await field.clean("valid", None) == "valid"
    with pytest.raises(FieldValidationError) as error:
        await field.clean("invalid", None)
    assert error.value.field_name == "label"
    with pytest.raises(TypeError, match="validator type failure"):
        await field.clean("type-error", None)
    with pytest.raises(FieldValidationError, match="async failure"):
        await ValidatedString(validators=[fail]).clean("valid", None)
    with pytest.raises(FieldValidationError, match="sync failure"):
        ValidatedString(validators=[sync_fail]).run_validators("valid")
    assert not field._is_async_validator(None)
    owner = SimpleNamespace(_model_field_types={})
    field.contribute_to_class(owner, "label", None)
    assert owner._model_field_types["label"] is field
    assert field.process_result_value("stored", None) == "stored"


@pytest.mark.parametrize("supplied_field", [None, "preassigned"])
def test_bind_validation_errors_keep_context(supplied_field):
    class FailingType(ValidatedString):
        def validate(self, value, model_instance=None):
            if value == "runtime":
                raise RuntimeError("binding failed")
            raise FieldValidationError("invalid binding", field_name=supplied_field)

    field = FailingType()
    field.attname = "label"
    with pytest.raises(FieldValidationError) as error:
        field.process_bind_param("invalid", None)
    assert error.value.field_name == (supplied_field or "label")
    with pytest.raises(FieldValidationError, match="binding failed"):
        field.process_bind_param("runtime", None)


def test_expression_value_equality_repr_and_hash():
    assert repr(Value(4)) == "Value(4)"
    assert Value(4) == Value(4)
    assert Value(4) != Value(5)
    assert Value(4) != 4
    assert hash(Value(4)) == hash(Value(4))
    with pytest.raises(AttributeError):
        Value(4).other = 5


def test_function_expression_guards_and_sql_operands():
    context = ResolverContext()
    assert str(_operand(column("x"), None, context)) == "x"
    assert _operand(4, None, context).value == 4
    with pytest.raises(TypeError, match="operand"):
        _operand(object(), None, context)
    with pytest.raises(ValueError):
        SQLFunction("untrusted")
    with pytest.raises(AttributeError):
        Abs(4).other = 5
    with pytest.raises(ValueError):
        Concat("only")
    assert str(Abs(-4).resolve(None)) == "abs(:param_1)"
    assert str(Cast(4, Integer).resolve(None)).startswith("CAST(")


@pytest.mark.parametrize(
    "factory,args",
    [
        (JSONHasKey, (F("x"), "")),
        (JSONContains, (F("x"), 1)),
        (ArrayIndex, (F("x"), True)),
        (ArrayIndex, (F("x"), -1)),
        (ArrayContains, (F("x"), 1)),
        (ArrayLength, (F("x"), 0)),
        (ArrayLength, (F("x"), True)),
        (_WindowFunction, ("unknown",)),
        (Lag, (F("x"), 0)),
        (Lead, (F("x"), False)),
        (Window, (1,)),
    ],
)
def test_invalid_typed_expression_arguments_are_rejected(factory, args):
    with pytest.raises((ValueError, TypeError)):
        factory(*args)


def test_window_rank_lead_and_expression_ordering_execute():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    try:
        rank = Window(Rank(), order_by=[Value(1)]).resolve(None)
        lead = Window(Lead(Value(3)), order_by=[Value(1)]).resolve(None)
        with engine.connect() as conn:
            assert conn.execute(select(rank, lead)).one() == (1, None)
    finally:
        engine.dispose()


@pytest.mark.parametrize("value", ["ab", {"a": 1}, [1], (1, 2, 3), 4])
def test_range_lookup_rejects_invalid_shapes(value):
    with pytest.raises(TypeError):
        _lookup_range(column("n"), value)


def test_range_iexact_and_datetime_lookup_execute():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.connect() as conn:
            assert conn.scalar(select(_lookup_range(literal(2), (1, 3))))
            assert conn.scalar(select(_lookup_iexact(literal("ADA"), "ada")))
            for part, value in [
                ("year", 2026),
                ("month", 11),
                ("day", 10),
                ("hour", 9),
                ("minute", 8),
                ("second", 7),
            ]:
                date = literal(datetime(2026, 11, 10, 9, 8, 7), type_=DateTime())
                assert conn.scalar(select(LOOKUP_OPERATORS[part](date, value)))
    finally:
        engine.dispose()
    with pytest.raises(TypeError):
        _lookup_iexact(column("name"), 2)
    fallback = SimpleNamespace(
        property=SimpleNamespace(columns=[column("at", DateTime())])
    )
    assert _lookup_date_part(fallback, "year", 2026) is not None
