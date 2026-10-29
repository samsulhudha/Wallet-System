import pytest
from decimal import Decimal
from apps.common.money import (
    decimal_to_minor_units,
    minor_units_to_decimal,
    format_money,
    MoneyValidationError,
    MAX_AMOUNT_MINOR_UNITS,
)


def test_valid_decimal_to_minor_units():
    assert decimal_to_minor_units("100.50", "INR") == 10050
    assert decimal_to_minor_units("0.01", "INR") == 1
    assert decimal_to_minor_units(Decimal("500.00"), "INR") == 50000
    assert decimal_to_minor_units("1000", "USD") == 100000


def test_minor_units_to_decimal():
    assert minor_units_to_decimal(10050, "INR") == Decimal("100.50")
    assert minor_units_to_decimal(1, "INR") == Decimal("0.01")
    assert minor_units_to_decimal(50000, "USD") == Decimal("500.00")


def test_format_money():
    assert format_money(10050, "INR") == "100.50 INR"
    assert format_money(2500, "USD") == "25.00 USD"


def test_disallow_float_input():
    with pytest.raises(MoneyValidationError, match="floating-point"):
        decimal_to_minor_units(100.50, "INR")


def test_disallow_negative_or_zero_amounts():
    with pytest.raises(MoneyValidationError, match="strictly positive"):
        decimal_to_minor_units("0.00", "INR")
    with pytest.raises(MoneyValidationError, match="strictly positive"):
        decimal_to_minor_units("-10.00", "INR")


def test_disallow_excessive_precision():
    with pytest.raises(MoneyValidationError, match="exceeds maximum precision"):
        decimal_to_minor_units("10.123", "INR")  # INR scale is 2


def test_disallow_unsupported_currency():
    with pytest.raises(MoneyValidationError, match="Unsupported currency"):
        decimal_to_minor_units("100.00", "XYZ")


def test_disallow_exceeding_max_limit():
    huge_amount = (MAX_AMOUNT_MINOR_UNITS // 100) + 1
    with pytest.raises(MoneyValidationError, match="exceeds maximum allowed"):
        decimal_to_minor_units(str(huge_amount), "INR")

