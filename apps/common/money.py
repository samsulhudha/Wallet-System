from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Union


CURRENCY_SCALES = {
    'INR': 2,
    'USD': 2,
    'EUR': 2,
    'GBP': 2,
}

SUPPORTED_CURRENCIES = set(CURRENCY_SCALES.keys())

# Max supported minor units: 10,000,000,000.00 (1 trillion minor units, fits safely in PostgreSQL BigInt)
MAX_AMOUNT_MINOR_UNITS = 1_000_000_000_000


class MoneyValidationError(ValueError):
    """Raised when monetary amount or currency validation fails."""
    pass


def get_currency_scale(currency: str) -> int:
    """Return number of decimal places (scale) for given currency."""
    currency_upper = currency.upper().strip()
    if currency_upper not in CURRENCY_SCALES:
        raise MoneyValidationError(f"Unsupported currency: {currency}. Supported: {sorted(SUPPORTED_CURRENCIES)}")
    return CURRENCY_SCALES[currency_upper]


def decimal_to_minor_units(amount: Union[str, int, float, Decimal], currency: str = 'INR') -> int:
    """
    Safely converts a decimal or string representation of money into integer minor units (e.g. paise/cents).
    Never uses float rounding directly; parses string or Decimal strictly.
    """
    currency_upper = currency.upper().strip()
    scale = get_currency_scale(currency_upper)

    if isinstance(amount, float):
        raise MoneyValidationError("Direct floating-point monetary input is disallowed. Provide string or Decimal.")

    try:
        dec_amount = Decimal(str(amount).strip())
    except (InvalidOperation, TypeError):
        raise MoneyValidationError(f"Invalid monetary format: {amount}")

    if dec_amount <= Decimal('0'):
        raise MoneyValidationError(f"Amount must be strictly positive: {amount}")

    if dec_amount.is_nan() or dec_amount.is_infinite():
        raise MoneyValidationError(f"Invalid numeric value: {amount}")

    # Check decimal precision
    exponent = dec_amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -scale:
        raise MoneyValidationError(f"Amount {amount} exceeds maximum precision of {scale} decimal places for {currency_upper}.")

    # Quantize to scale
    quantum = Decimal('10') ** -scale
    quantized = dec_amount.quantize(quantum, rounding=ROUND_HALF_UP)
    minor_units = int((quantized * (Decimal('10') ** scale)).to_integral_value())

    if minor_units <= 0:
        raise MoneyValidationError("Amount in minor units must be greater than zero.")

    if minor_units > MAX_AMOUNT_MINOR_UNITS:
        raise MoneyValidationError(f"Amount {amount} exceeds maximum allowed transaction limit.")

    return minor_units


def minor_units_to_decimal(minor_units: int, currency: str = 'INR') -> Decimal:
    """Converts integer minor units back to Decimal with correct scale."""
    if not isinstance(minor_units, int):
        raise MoneyValidationError(f"Minor units must be an integer, got {type(minor_units)}")
    scale = get_currency_scale(currency)
    divisor = Decimal('10') ** scale
    return (Decimal(minor_units) / divisor).quantize(Decimal('10') ** -scale)


def format_money(minor_units: int, currency: str = 'INR') -> str:
    """Formats minor units to a human-readable string, e.g., '100.50 INR'."""
    dec = minor_units_to_decimal(minor_units, currency)
    return f"{dec:.2f} {currency.upper()}"

