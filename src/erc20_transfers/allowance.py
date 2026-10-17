"""The checks that belong in front of every `transferFrom`.

`transferFrom` moves someone else's tokens, and it needs two things at once:
an allowance large enough for the amount, and an owner whose balance actually
holds it. Miss either and the call reverts, and the two reverts are hard to
tell apart once they have happened - the same failed transaction, the same
spent gas, and a revert string that depends on the token. Told apart before
sending they point at different fixes: a short allowance is one `approve`
away, a short balance is tokens that are not there and no approval conjures
them.

The revert is also easy to misread: `eth_call` (`.call()` in web3.py) runs the
call against a local copy of state and hands back a simulated return value.
Nothing is signed, nothing reaches a mempool, no receipt is produced. A `True`
from `.call()` says "this would have worked"; it never says "the tokens
moved".

So both numbers are read first and reported with the amount they were measured
against, before any call data is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from erc20_transfers.abi import UINT256_MAX, encode_transfer_from, normalize_address

UNLIMITED_ALLOWANCE: Final = UINT256_MAX

ShortSide = Literal["allowance", "balance", "both"]


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
    """Raised instead of sending a `transferFrom` the approval does not cover."""

    def __init__(self, check: AllowanceCheck | FundsCheck) -> None:
        super().__init__(check.explain())
        self.check = check


class InsufficientBalance(RuntimeError):
    """Raised instead of sending a `transferFrom` the owner cannot fund."""

    def __init__(self, check: FundsCheck) -> None:
        super().__init__(check.explain())
        self.check = check


@dataclass(frozen=True, slots=True)
class FundsCheck:
    """Both preconditions of a `transferFrom`, with the numbers behind each."""

    owner: str
    spender: str
    required: int
    allowance: int
    balance: int

    @property
    def allowance_check(self) -> AllowanceCheck:
        """The allowance side on its own."""
        return AllowanceCheck(
            owner=self.owner,
            spender=self.spender,
            required=self.required,
            current=self.allowance,
        )

    @property
    def allowance_sufficient(self) -> bool:
        return self.allowance >= self.required

    @property
    def balance_sufficient(self) -> bool:
        return self.balance >= self.required

    @property
    def sufficient(self) -> bool:
        return self.allowance_sufficient and self.balance_sufficient

    @property
    def allowance_shortfall(self) -> int:
        return max(self.required - self.allowance, 0)

    @property
    def balance_shortfall(self) -> int:
        return max(self.required - self.balance, 0)

    @property
    def unlimited_allowance(self) -> bool:
        return self.allowance == UNLIMITED_ALLOWANCE

    @property
    def short_side(self) -> ShortSide | None:
        """Which side would revert the call; `None` when neither would."""
        if self.sufficient:
            return None
        if not self.allowance_sufficient and not self.balance_sufficient:
            return "both"

        return "allowance" if not self.allowance_sufficient else "balance"

    def explain(self) -> str:
        """Say in one sentence which side is short, and by how much."""
        granted = (
            "an unlimited allowance"
            if self.unlimited_allowance
            else f"an allowance of {self.allowance}"
        )
        side = self.short_side
        if side is None:
            return (
                f"{self.owner} holds {self.balance} and {self.spender} holds "
                f"{granted}; transferFrom of {self.required} is covered on both sides"
            )
        if side == "both":
            return (
                f"transferFrom of {self.required} is short on both sides: "
                f"{self.owner} holds {self.balance} and needs "
                f"{self.balance_shortfall} more, while {self.spender} holds "
                f"{granted} and needs {self.allowance_shortfall} more; approving "
                f"more does not make the missing tokens appear"
            )
        if side == "balance":
            return (
                f"{self.spender} holds {granted} from {self.owner}, enough for "
                f"{self.required}, but {self.owner} holds only {self.balance} and "
                f"needs {self.balance_shortfall} more; transferFrom reverts on the "
                f"balance, not on the allowance"
            )

        return (
            f"{self.owner} holds {self.balance}, enough for {self.required}, but "
            f"{self.spender} holds {granted} and needs {self.allowance_shortfall} "
            f"more; transferFrom reverts on the allowance, not on the balance"
        )

    def raise_for_funds(self) -> None:
        """Raise the short side, the balance first when both are short.

        The balance is named ahead of the allowance because it is the side an
        `approve` cannot fix.
        """
        if not self.balance_sufficient:
            raise InsufficientBalance(self)
        if not self.allowance_sufficient:
            raise InsufficientAllowance(self)


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


def check_funds(
    *, owner: str, spender: str, required: int, allowance: int, balance: int
) -> FundsCheck:
    """Compare both sides of a `transferFrom` against the amount to be moved.

    `allowance` is the decoded return value of `allowance(owner, spender)`,
    `balance` the decoded return value of `balanceOf(owner)`.
    """
    return FundsCheck(
        owner=normalize_address(owner),
        spender=normalize_address(spender),
        required=_uint256(required, "required"),
        allowance=_uint256(allowance, "allowance"),
        balance=_uint256(balance, "balance"),
    )


def require_funds(
    *, owner: str, spender: str, required: int, allowance: int, balance: int
) -> FundsCheck:
    """Like `check_funds`, but raise the side that would revert the call."""
    check = check_funds(
        owner=owner,
        spender=spender,
        required=required,
        allowance=allowance,
        balance=balance,
    )
    check.raise_for_funds()

    return check


def encode_checked_transfer_from(
    *, sender: str, spender: str, to: str, amount: int, allowance: int, balance: int
) -> bytes:
    """Call data for `transferFrom`, refused when either side falls short.

    `sender` is the owner the tokens leave, `spender` the account that signs
    the transaction, `allowance` the value `allowance(sender, spender)`
    returned and `balance` the value `balanceOf(sender)` returned. A short
    allowance raises `InsufficientAllowance`, a short balance
    `InsufficientBalance`; the chain reverts alike for both, which is why they
    are told apart here.
    """
    require_funds(
        owner=sender,
        spender=spender,
        required=amount,
        allowance=allowance,
        balance=balance,
    )

    return encode_transfer_from(sender=sender, to=to, amount=amount)
