"""The allowance check that belongs in front of every `transferFrom`.

`transferFrom` moves someone else's tokens, and it only works while that
someone has approved the spender for at least the amount being moved. Without
the approval the call reverts, and the revert is easy to misread: `eth_call`
(`.call()` in web3.py) runs the call against a local copy of state and hands
back a simulated return value. Nothing is signed, nothing reaches a mempool,
no receipt is produced. A `True` from `.call()` says "this would have worked";
it never says "the tokens moved".

So the allowance is read first and reported with the numbers in it, before any
call data is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from erc20_transfers.abi import UINT256_MAX, encode_transfer_from, normalize_address

UNLIMITED_ALLOWANCE: Final = UINT256_MAX


def _uint256(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    if value > UINT256_MAX:
        raise ValueError(f"{name} exceeds uint256 range: {value}")

    return value


@dataclass(frozen=True, slots=True)
class AllowanceCheck:
    """What `spender` is allowed to move of `owner`'s tokens, against what it needs."""

    owner: str
    spender: str
    required: int
    current: int

    @property
    def sufficient(self) -> bool:
        return self.current >= self.required

    @property
    def shortfall(self) -> int:
        return max(self.required - self.current, 0)

    @property
    def unlimited(self) -> bool:
        return self.current == UNLIMITED_ALLOWANCE

    def explain(self) -> str:
        """Say in one sentence whether the transfer is covered, and by how much."""
        if self.sufficient:
            granted = (
                "an unlimited allowance"
                if self.unlimited
                else f"an allowance of {self.current}"
            )
            return (
                f"{self.spender} holds {granted} from {self.owner}; "
                f"transferFrom of {self.required} is covered"
            )

        return (
            f"{self.spender} holds an allowance of {self.current} from {self.owner}, "
            f"but transferFrom of {self.required} needs {self.shortfall} more; "
            f"the call reverts until the owner approves at least {self.required}"
        )

    def raise_for_allowance(self) -> None:
        """Raise `InsufficientAllowance` unless the transfer is covered."""
        if not self.sufficient:
            raise InsufficientAllowance(self)


class InsufficientAllowance(RuntimeError):
    """Raised instead of sending a `transferFrom` that would revert."""

    def __init__(self, check: AllowanceCheck) -> None:
        super().__init__(check.explain())
        self.check = check


def check_allowance(
    *, owner: str, spender: str, required: int, current: int
) -> AllowanceCheck:
    """Compare an allowance read from the chain against the amount to be moved.

    `current` is the decoded return value of `allowance(owner, spender)`.
    """
    return AllowanceCheck(
        owner=normalize_address(owner),
        spender=normalize_address(spender),
        required=_uint256(required, "required"),
        current=_uint256(current, "current"),
    )


def require_allowance(
    *, owner: str, spender: str, required: int, current: int
) -> AllowanceCheck:
    """Like `check_allowance`, but raise `InsufficientAllowance` when short."""
    check = check_allowance(
        owner=owner, spender=spender, required=required, current=current
    )
    check.raise_for_allowance()

    return check


def encode_checked_transfer_from(
    *, sender: str, spender: str, to: str, amount: int, allowance: int
) -> bytes:
    """Call data for `transferFrom`, refused when the allowance does not cover it.

    `sender` is the owner the tokens leave, `spender` the account that signs
    the transaction, `allowance` the value `allowance(sender, spender)`
    returned.
    """
    require_allowance(
        owner=sender, spender=spender, required=amount, current=allowance
    )

    return encode_transfer_from(sender=sender, to=to, amount=amount)
