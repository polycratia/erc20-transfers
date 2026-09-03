"""The three ways an ERC-20 token deviates in practice, each named and handled.

The standard is an interface, not an enforcement. Nothing stops a token from
returning nothing where a `bool` is specified, from delivering less than was
sent, or from rejecting an `approve` that overwrites a non-zero allowance -
and the tokens that do these things are among the most traded there are.

Code written against the happy path reads a missing return value as a failure,
credits a recipient with an amount that never arrived, and broadcasts
approvals that revert. So each deviation is handled where the behaviour is
knowable and refused where it is not: return data that is neither empty nor a
boolean word is not guessed at, and a fee is only ever learned by comparing
balances around the transfer, never from the amount that was asked for.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from erc20_transfers.abi import (
    UINT256_MAX,
    WORD_SIZE,
    encode_address,
    encode_uint256,
    normalize_address,
)

__all__ = [
    "APPROVE_SELECTOR",
    "ApprovalPlan",
    "ApprovalStep",
    "FeeOnTransfer",
    "NonBooleanReturn",
    "TransferReceipt",
    "TransferRejected",
    "TransferReturn",
    "ZERO_FIRST_ALLOWANCE_TOKENS",
    "encode_approve",
    "measure_received",
    "plan_approval",
    "read_transfer_return",
    "requires_zero_first_allowance",
]

APPROVE_SELECTOR: Final = bytes.fromhex("095ea7b3")  # approve(address,uint256)

_BPS_PER_UNIT: Final = 10_000

# Tokens known to revert an `approve` that moves a non-zero allowance straight
# to another non-zero value. The list answers "this token forces the reset";
# it never answers "this token is safe without one".
ZERO_FIRST_ALLOWANCE_TOKENS: Final[Mapping[str, str]] = MappingProxyType(
    {"0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT (Ethereum mainnet)"}
)


def _uint256(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    if value > UINT256_MAX:
        raise ValueError(f"{name} exceeds uint256 range: {value}")

    return value


def encode_approve(*, spender: str, amount: int) -> bytes:
    """Call data for `approve(spender, amount)`.

    Whether one approve is enough to raise a non-zero allowance depends on the
    token; `plan_approval` decides that and orders the calls.
    """
    return APPROVE_SELECTOR + encode_address(spender) + encode_uint256(amount)


class NonBooleanReturn(ValueError):
    """Raised for return data that is neither empty nor a single boolean word."""


class TransferRejected(RuntimeError):
    """Raised when a transfer reported failure by returning `false`."""

    def __init__(self, result: TransferReturn) -> None:
        super().__init__(result.explain())
        self.result = result


@dataclass(frozen=True, slots=True)
class TransferReturn:
    """What `transfer` or `transferFrom` gave back, including giving back nothing."""

    data: bytes
    value: bool | None

    @property
    def silent(self) -> bool:
        """Whether the token returned no data at all."""
        return self.value is None

    @property
    def succeeded(self) -> bool:
        """Whether the tokens moved, reading silence as success."""
        return True if self.value is None else self.value

    def explain(self) -> str:
        """Say in one sentence what the token reported, and what it did not."""
        if self.value is None:
            return (
                "the token returned nothing; it predates the finalised standard, "
                "so the call not reverting is the only success signal there is"
            )
        if self.value:
            return "the token returned true; the transfer went through"

        return (
            "the token returned false without reverting; the tokens did not move, "
            "the gas was still spent, and the receipt still carries status 1"
        )

    def raise_for_status(self) -> None:
        """Raise `TransferRejected` when the token reported failure."""
        if not self.succeeded:
            raise TransferRejected(self)


def read_transfer_return(data: bytes) -> TransferReturn:
    """Interpret the return data of `transfer` or `transferFrom`.

    Empty data is a success: USDT and its contemporaries were written before
    the return value was part of the standard and revert on failure instead.
    Anything that is neither empty nor a boolean word is refused rather than
    read as truthy, because a token returning something else is not described
    by the interface it claims to implement.
    """
    raw = bytes(data)
    if not raw:
        return TransferReturn(data=b"", value=None)
    if len(raw) != WORD_SIZE:
        raise NonBooleanReturn(
            f"expected {WORD_SIZE} bytes of return data or none at all, "
            f"got {len(raw)}: 0x{raw.hex()}"
        )

    word = int.from_bytes(raw, "big")
    if word > 1:
        raise NonBooleanReturn(
            f"return data 0x{raw.hex()} is neither empty nor a boolean; "
            f"what this token means by it is not guessed at here"
        )

    return TransferReturn(data=raw, value=word == 1)


class FeeOnTransfer(RuntimeError):
    """Raised when less arrived than was sent."""

    def __init__(self, receipt: TransferReceipt) -> None:
        super().__init__(receipt.explain())
        self.receipt = receipt


@dataclass(frozen=True, slots=True)
class TransferReceipt:
    """How much of a transfer arrived, measured rather than assumed."""

    sent: int
    received: int

    def __post_init__(self) -> None:
        _uint256(self.sent, "sent")
        _uint256(self.received, "received")
        if self.received > self.sent:
            raise ValueError(
                f"{self.received} arrived out of {self.sent} sent; a balance that "
                f"grew by more than the transfer means a rebase or a second "
                f"transfer landed in between, so this measurement says nothing"
            )

    @property
    def fee(self) -> int:
        """What the token kept on the way through."""
        return self.sent - self.received

    @property
    def has_fee(self) -> bool:
        return self.received < self.sent

    @property
    def fee_bps(self) -> Decimal:
        """The share the token kept, in basis points of what was sent."""
        if self.sent == 0:
            return Decimal(0)

        return Decimal(self.fee * _BPS_PER_UNIT) / Decimal(self.sent)

    def explain(self) -> str:
        """Say in one sentence what arrived and what was taken."""
        if not self.has_fee:
            return f"all {self.sent} units arrived; this token takes no fee"

        return (
            f"{self.received} of {self.sent} units arrived; the token kept "
            f"{self.fee} ({self.fee_bps:.2f} bps), so anything downstream that was "
            f"promised {self.sent} is short"
        )

    def raise_for_fee(self) -> None:
        """Raise `FeeOnTransfer` unless the full amount arrived."""
        if self.has_fee:
            raise FeeOnTransfer(self)


def measure_received(
    *, sent: int, balance_before: int, balance_after: int
) -> TransferReceipt:
    """Work out what arrived, from the recipient's balance around the transfer.

    A fee-taking token deducts on the way through, and the deduction is in
    neither the call data nor the receipt, nor derivable from the amount: the
    only honest source is `balanceOf(recipient)` read before the transfer and
    again after it. Grossing an amount up so the net comes out exact is not
    offered, because inverting a fee schedule nobody published is guesswork.
    """
    before = _uint256(balance_before, "balance_before")
    after = _uint256(balance_after, "balance_after")
    if after < before:
        raise ValueError(
            f"the recipient balance fell from {before} to {after} across a transfer "
            f"in; the two reads are not around the same transfer"
        )

    return TransferReceipt(sent=_uint256(sent, "sent"), received=after - before)


def requires_zero_first_allowance(token: str) -> bool:
    """Whether this token is known to reject a non-zero to non-zero `approve`.

    A false answer means the token is not on the list, not that overwriting
    its allowance directly is safe; the list is short and mainnet-only.
    """
    return normalize_address(token) in ZERO_FIRST_ALLOWANCE_TOKENS


@dataclass(frozen=True, slots=True)
class ApprovalStep:
    """One `approve` transaction: the value it sets, its call data, and why."""

    value: int
    data: bytes
    reason: str


@dataclass(frozen=True, slots=True)
class ApprovalPlan:
    """The approvals to broadcast, in order, before a `transferFrom` can work."""

    spender: str
    current: int
    required: int
    steps: tuple[ApprovalStep, ...]

    @property
    def needed(self) -> bool:
        """Whether anything has to be sent at all."""
        return bool(self.steps)

    @property
    def resets_first(self) -> bool:
        """Whether the allowance goes through zero on the way up."""
        return len(self.steps) == 2

    def call_data(self) -> tuple[bytes, ...]:
        """The call data of every step, in the order it must be broadcast."""
        return tuple(step.data for step in self.steps)

    def explain(self) -> str:
        """Say in one sentence what to send, and why there may be two of them."""
        if not self.steps:
            if self.required == 0:
                return f"{self.spender} holds no allowance; there is nothing to revoke"

            return (
                f"{self.spender} already holds an allowance of {self.current}, "
                f"which covers {self.required}; no approve is needed"
            )
        if len(self.steps) == 1:
            return f"one transaction: {self.steps[0].reason}"

        return (
            f"two transactions: set the allowance of {self.current} to zero, then "
            f"approve {self.required}; a token that rejects a non-zero to non-zero "
            f"approve accepts nothing else, and the reset also closes the window in "
            f"which {self.spender} could spend the old allowance and the new one"
        )


def plan_approval(
    *, spender: str, current: int, required: int, reset_first: bool = True
) -> ApprovalPlan:
    """The approve transactions that put `required` within `spender`'s reach.

    `current` is what `allowance(owner, spender)` returned. USDT and a handful
    of others reject an `approve` that moves a non-zero allowance to another
    non-zero value - the check sits in their source and the transaction
    reverts - so the allowance goes to zero first and up afterwards. That
    detour works on every token and costs one extra transaction, which is why
    it is the default; `requires_zero_first_allowance` says when it is not
    optional and `reset_first=False` skips it.

    Only raising the allowance is planned: one that already covers `required`
    is left alone. To lower an allowance, plan for `required=0` and approve
    the new value afterwards.
    """
    account = normalize_address(spender)
    have = _uint256(current, "current")
    want = _uint256(required, "required")
    steps: tuple[ApprovalStep, ...]

    if want == 0:
        steps = (
            ()
            if have == 0
            else (
                ApprovalStep(
                    value=0,
                    data=encode_approve(spender=account, amount=0),
                    reason=f"revoke the allowance of {have}",
                ),
            )
        )
    elif have >= want:
        steps = ()
    elif have == 0 or not reset_first:
        steps = (
            ApprovalStep(
                value=want,
                data=encode_approve(spender=account, amount=want),
                reason=f"approve {want}",
            ),
        )
    else:
        steps = (
            ApprovalStep(
                value=0,
                data=encode_approve(spender=account, amount=0),
                reason=f"reset the allowance of {have} to zero",
            ),
            ApprovalStep(
                value=want,
                data=encode_approve(spender=account, amount=want),
                reason=f"approve {want}",
            ),
        )

    return ApprovalPlan(spender=account, current=have, required=want, steps=steps)
