"""EIP-1559 fees, taken from recent blocks and bounded by the caller's ceiling.

A price hardcoded years ago is a legacy `gasPrice`: one number, chosen once,
wrong within the hour. Since London a transaction names two instead -
`maxFeePerGas`, the most it will pay per unit of gas, and
`maxPriorityFeePerGas`, the part of that which reaches the block proposer.
The rest is the base fee: set by the protocol, burned, moved by up to 1/8 per
block, and refunded to the sender above what the block actually charged.
Overpaying on `maxFeePerGas` therefore costs nothing but exposure; underpaying
costs inclusion.

So both numbers are derived from what the chain just did - the pending base
fee grown by the headroom the caller asks for, plus the tip recent blocks
actually paid - and neither is allowed to run away: `max_fee_ceiling` is a
required argument, and a ceiling under the base fee raises instead of
producing a transaction that can never be included.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from erc20_transfers.abi import UINT256_MAX
from erc20_transfers.units import to_units

__all__ = [
    "BASE_FEE_MAX_CHANGE_DENOMINATOR",
    "DEFAULT_HEADROOM_BLOCKS",
    "ELASTICITY_MULTIPLIER",
    "Eip1559Fees",
    "FeeCeilingTooLow",
    "GWEI",
    "MAX_HEADROOM_BLOCKS",
    "base_fee_headroom",
    "estimate_fees",
    "gwei",
    "next_base_fee",
    "priority_fee_from_history",
]

GWEI: Final = 10**9
_GWEI_DECIMALS: Final = 9

# A block may move the base fee by at most 1/8 of it, towards a target of half
# the block gas limit. Both numbers are protocol constants from EIP-1559.
BASE_FEE_MAX_CHANGE_DENOMINATOR: Final = 8
ELASTICITY_MULTIPLIER: Final = 2

# Two blocks of maximum growth is ~27% of headroom, which covers the usual
# wait without bidding against a spike that has not happened.
DEFAULT_HEADROOM_BLOCKS: Final = 2

# Past this the headroom stops meaning anything: 64 full blocks multiply the
# base fee by more than a thousand.
MAX_HEADROOM_BLOCKS: Final = 64


def _wei(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    if value > UINT256_MAX:
        raise ValueError(f"{name} exceeds uint256 range: {value}")

    return value


def _format_gwei(value: int) -> str:
    return f"{Decimal(value).scaleb(-_GWEI_DECIMALS).normalize():f}"


def gwei(amount: Decimal | int | str) -> int:
    """Convert a gwei amount to wei, exactly.

    Fees are quoted in gwei and paid in wei; a ceiling written as `gwei(60)`
    is harder to get wrong by three zeros than `60_000_000_000`.
    """
    return to_units(amount, decimals=_GWEI_DECIMALS)


def next_base_fee(*, base_fee_per_gas: int, gas_used: int, gas_limit: int) -> int:
    """The base fee the block after this one will charge.

    The protocol computes it from the block that just closed, so it is known
    exactly rather than estimated: a full block raises the fee by 1/8, an
    empty one lowers it by the same, and a half-full block leaves it alone.
    """
    base = _wei(base_fee_per_gas, "base_fee_per_gas")
    used = _wei(gas_used, "gas_used")
    limit = _wei(gas_limit, "gas_limit")
    if limit < ELASTICITY_MULTIPLIER:
        raise ValueError(f"gas_limit must be at least {ELASTICITY_MULTIPLIER}, got {limit}")
    if used > limit:
        raise ValueError(f"gas_used {used} exceeds gas_limit {limit}")

    target = limit // ELASTICITY_MULTIPLIER
    if used == target:
        return base
    if used > target:
        delta = base * (used - target) // target // BASE_FEE_MAX_CHANGE_DENOMINATOR
        return base + max(delta, 1)

    delta = base * (target - used) // target // BASE_FEE_MAX_CHANGE_DENOMINATOR
    return base - delta


def base_fee_headroom(
    base_fee_per_gas: int, *, blocks: int = DEFAULT_HEADROOM_BLOCKS
) -> int:
    """The highest base fee reachable `blocks` blocks from now.

    Every block in a row would have to be full to get there, so this is a
    bound rather than a forecast; paying under it is what keeps a transaction
    includable while it waits.
    """
    value = _wei(base_fee_per_gas, "base_fee_per_gas")
    if isinstance(blocks, bool) or not isinstance(blocks, int):
        raise TypeError(f"blocks must be an int, got {type(blocks).__name__}")
    if blocks < 0 or blocks > MAX_HEADROOM_BLOCKS:
        raise ValueError(f"blocks must be between 0 and {MAX_HEADROOM_BLOCKS}, got {blocks}")

    for _ in range(blocks):
        value += value // BASE_FEE_MAX_CHANGE_DENOMINATOR
    if value > UINT256_MAX:
        raise ValueError(f"base fee over {blocks} blocks exceeds uint256 range: {value}")

    return value


def priority_fee_from_history(rewards: Sequence[int]) -> int:
    """The tip recent blocks actually paid, as the median of their rewards.

    `rewards` is one percentile column of the `reward` array `eth_feeHistory`
    returns - one value in wei per block, already decoded from hex. The median
    and not the mean, so that a single block which paid ten times the going
    rate does not set the price for the next one.
    """
    samples = sorted(_wei(value, "reward") for value in rewards)
    if not samples:
        raise ValueError("rewards is empty; fee history covering no block prices nothing")

    middle = len(samples) // 2
    if len(samples) % 2 == 1:
        return samples[middle]

    return (samples[middle - 1] + samples[middle]) // 2


class FeeCeilingTooLow(ValueError):
    """Raised instead of returning fees a block would refuse."""

    def __init__(self, *, base_fee_per_gas: int, max_fee_ceiling: int) -> None:
        super().__init__(
            f"a ceiling of {_format_gwei(max_fee_ceiling)} gwei is below the base fee "
            f"of {_format_gwei(base_fee_per_gas)} gwei; a transaction whose maxFeePerGas "
            f"is under the base fee is never included, so raise the ceiling or wait "
            f"for the base fee to fall"
        )
        self.base_fee_per_gas = base_fee_per_gas
        self.max_fee_ceiling = max_fee_ceiling


@dataclass(frozen=True, slots=True)
class Eip1559Fees:
    """The two fee fields of a type-2 transaction, with what they were derived from."""

    base_fee_per_gas: int
    max_fee_per_gas: int
    max_priority_fee_per_gas: int
    max_fee_ceiling: int
    uncapped_max_fee_per_gas: int

    @property
    def capped(self) -> bool:
        """Whether the ceiling, not the chain, decided `max_fee_per_gas`."""
        return self.uncapped_max_fee_per_gas > self.max_fee_ceiling

    @property
    def headroom(self) -> int:
        """Wei per gas the base fee may still rise before the tip is squeezed."""
        return (
            self.max_fee_per_gas
            - self.base_fee_per_gas
            - self.max_priority_fee_per_gas
        )

    def as_transaction_fields(self) -> dict[str, int]:
        """The fields to merge into a transaction before signing it."""
        return {
            "maxFeePerGas": self.max_fee_per_gas,
            "maxPriorityFeePerGas": self.max_priority_fee_per_gas,
        }

    def explain(self) -> str:
        """Say in one sentence what will be paid, and whether the ceiling bound it."""
        line = (
            f"base fee {_format_gwei(self.base_fee_per_gas)} gwei, paying up to "
            f"{_format_gwei(self.max_fee_per_gas)} gwei with a "
            f"{_format_gwei(self.max_priority_fee_per_gas)} gwei tip"
        )
        if not self.capped:
            return line

        return (
            f"{line}; the ceiling of {_format_gwei(self.max_fee_ceiling)} gwei cut "
            f"that back from {_format_gwei(self.uncapped_max_fee_per_gas)} gwei, so "
            f"the transaction waits if the base fee keeps climbing"
        )


def estimate_fees(
    *,
    base_fee_per_gas: int,
    priority_fee_per_gas: int,
    max_fee_ceiling: int,
    headroom_blocks: int = DEFAULT_HEADROOM_BLOCKS,
) -> Eip1559Fees:
    """Price a type-2 transaction from the pending base fee and a recent tip.

    `base_fee_per_gas` is what the next block will charge - the last entry of
    `eth_feeHistory`'s `baseFeePerGas`, or `next_base_fee` over a block that
    just closed. `priority_fee_per_gas` is the tip, usually from
    `priority_fee_from_history`. `max_fee_ceiling` is the caller's own limit
    in wei per gas; the returned `max_fee_per_gas` never exceeds it, and the
    tip is trimmed to what fits underneath rather than being advertised at a
    value the transaction cannot honour.
    """
    base = _wei(base_fee_per_gas, "base_fee_per_gas")
    tip = _wei(priority_fee_per_gas, "priority_fee_per_gas")
    ceiling = _wei(max_fee_ceiling, "max_fee_ceiling")
    if ceiling < base:
        raise FeeCeilingTooLow(base_fee_per_gas=base, max_fee_ceiling=ceiling)

    uncapped = base_fee_headroom(base, blocks=headroom_blocks) + tip
    if uncapped > UINT256_MAX:
        raise ValueError(f"maxFeePerGas exceeds uint256 range: {uncapped}")
    max_fee = min(uncapped, ceiling)

    return Eip1559Fees(
        base_fee_per_gas=base,
        max_fee_per_gas=max_fee,
        max_priority_fee_per_gas=min(tip, max_fee - base),
        max_fee_ceiling=ceiling,
        uncapped_max_fee_per_gas=uncapped,
    )
