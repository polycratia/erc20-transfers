"""Human amounts and token units, converted with the decimals a token reports.

Eighteen decimals is a convention, not a rule: USDT and USDC use six, WBTC
uses eight, and a handful of tokens use none at all. Hardcoding 18 against a
six-decimal token scales every amount by 10**12 - the difference between
paying out 1.5 USDT and paying out 1.5 million of them.

So nothing here has a default. `decimals` is a required argument, read from
the token itself with the `decimals()` call, and conversion is exact: amounts
are `Decimal`, floats are refused, and an amount finer than the token can
represent raises instead of quietly rounding away the remainder.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Final

from erc20_transfers.abi import UINT256_MAX, WORD_SIZE, decode_uint256

__all__ = [
    "DECIMALS_SELECTOR",
    "MAX_DECIMALS",
    "TokenAmount",
    "decode_decimals",
    "encode_decimals",
    "from_units",
    "to_units",
]

DECIMALS_SELECTOR: Final = bytes.fromhex("313ce567")  # decimals()

# `decimals()` returns a uint8, so this is the whole range a token can claim.
MAX_DECIMALS: Final = 255

# Enough digits for a uint256 (78) plus the largest exponent a token can ask
# for, so scaling never rounds under the default context precision of 28.
_PRECISION: Final = 512


def encode_decimals() -> bytes:
    """Call data for the `decimals()` read."""
    return DECIMALS_SELECTOR


def decode_decimals(data: bytes) -> int:
    """Decode the return data of `decimals()`."""
    value = decode_uint256(data)
    if value > MAX_DECIMALS:
        raise ValueError(
            f"decimals() must return a uint8, got {value}: 0x{bytes(data).hex()}"
        )

    return value


def _check_decimals(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"decimals must be an int, got {type(value).__name__}")
    if value < 0 or value > MAX_DECIMALS:
        raise ValueError(f"decimals must be between 0 and {MAX_DECIMALS}, got {value}")

    return value


def _as_decimal(amount: Decimal | int | str) -> Decimal:
    if isinstance(amount, float):
        raise TypeError("amount must not be a float; use Decimal, int or str")
    if isinstance(amount, bool):
        raise TypeError("amount must not be a bool")
    if isinstance(amount, Decimal):
        value = amount
    else:
        try:
            value = Decimal(amount)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise TypeError(f"amount is not a number: {amount!r}") from exc
    if not value.is_finite():
        raise ValueError(f"amount must be finite, got {value}")

    return value


def to_units(amount: Decimal | int | str, *, decimals: int) -> int:
    """Convert a human amount into the integer the contract expects.

    `decimals` is what the token returned from `decimals()`. An amount with
    more precision than the token can hold - 1.0000005 against six decimals -
    is refused, because the alternative is losing the tail without saying so.
    """
    exponent = _check_decimals(decimals)
    value = _as_decimal(amount)
    if value < 0:
        raise ValueError(f"amount must not be negative, got {value}")

    with localcontext() as ctx:
        ctx.prec = _PRECISION
        scaled = value.scaleb(exponent)
        whole = scaled.to_integral_value()
        if scaled != whole:
            raise ValueError(
                f"{value} needs more than {exponent} decimals; "
                f"this token cannot represent it exactly"
            )
        units = int(whole)

    if units > UINT256_MAX:
        raise ValueError(f"amount exceeds uint256 range: {value} at {exponent} decimals")

    return units


def from_units(units: int, *, decimals: int) -> Decimal:
    """Convert an integer read from the contract back into a human amount.

    `units` is a decoded `balanceOf`, `allowance` or transfer value; the
    result carries exactly `decimals` fractional digits.
    """
    exponent = _check_decimals(decimals)
    if isinstance(units, bool) or not isinstance(units, int):
        raise TypeError(f"units must be an int, got {type(units).__name__}")
    if units < 0:
        raise ValueError(f"units must not be negative, got {units}")
    if units > UINT256_MAX:
        raise ValueError(f"units exceeds uint256 range: {units}")

    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return Decimal(units).scaleb(-exponent)


@dataclass(frozen=True, slots=True)
class TokenAmount:
    """An on-chain integer that kept the scale it was read at.

    Carrying `units` alone invites the mistake this module exists to prevent:
    the number means nothing until the token says how to read it.
    """

    units: int
    decimals: int

    def __post_init__(self) -> None:
        _check_decimals(self.decimals)
        if isinstance(self.units, bool) or not isinstance(self.units, int):
            raise TypeError(f"units must be an int, got {type(self.units).__name__}")
        if self.units < 0:
            raise ValueError(f"units must not be negative, got {self.units}")
        if self.units > UINT256_MAX:
            raise ValueError(f"units exceeds uint256 range: {self.units}")

    @classmethod
    def of(cls, amount: Decimal | int | str, *, decimals: int) -> TokenAmount:
        """Build from a human amount, scaled by the token's own decimals."""
        return cls(units=to_units(amount, decimals=decimals), decimals=decimals)

    @classmethod
    def from_return_data(cls, data: bytes, *, decimals: int) -> TokenAmount:
        """Build from a `balanceOf` or `allowance` word plus the token's decimals."""
        if len(bytes(data)) != WORD_SIZE:
            raise ValueError(
                f"expected {WORD_SIZE} bytes of return data, got {len(bytes(data))}"
            )

        return cls(units=decode_uint256(data), decimals=decimals)

    @property
    def amount(self) -> Decimal:
        """The value as a person reads it."""
        return from_units(self.units, decimals=self.decimals)

    def __str__(self) -> str:
        return f"{self.amount:f}"
