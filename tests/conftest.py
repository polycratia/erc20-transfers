"""A scripted stand-in for a node: enough of `eth_call`, sending a transaction
and `eth_feeHistory` to drive the package end to end without a network.

The tokens here are deliberately not all well behaved. One returns nothing from
a transfer, one keeps a cut on the way through, and one rejects an `approve`
that moves a non-zero allowance straight to another non-zero value - the three
deviations the package exists to handle. The double decodes call data the way a
contract would and reverts where a contract would, so a test that passes here
failed for a reason a chain would also have had.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import pytest

from erc20_transfers import (
    ALLOWANCE_SELECTOR,
    APPROVE_SELECTOR,
    BALANCE_OF_SELECTOR,
    DECIMALS_SELECTOR,
    TRANSFER_FROM_SELECTOR,
    TRANSFER_SELECTOR,
    UINT256_MAX,
    normalize_address,
)

WORD_SIZE = 32
BPS_PER_UNIT = 10_000

ALICE = "0x1111111111111111111111111111111111111111"
BOB = "0x2222222222222222222222222222222222222222"
CAROL = "0x3333333333333333333333333333333333333333"

DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
FEE_TOKEN = "0x4444444444444444444444444444444444444444"


class Revert(RuntimeError):
    """What a node reports when a call reverts: no state change, gas spent."""


def _word(value: int) -> bytes:
    return value.to_bytes(WORD_SIZE, "big")


def _uint(args: bytes, index: int) -> int:
    chunk = args[index * WORD_SIZE : (index + 1) * WORD_SIZE]
    if len(chunk) != WORD_SIZE:
        raise Revert(f"call data is short of argument {index}")

    return int.from_bytes(chunk, "big")


def _address(args: bytes, index: int) -> str:
    chunk = args[index * WORD_SIZE : (index + 1) * WORD_SIZE]
    if len(chunk) != WORD_SIZE:
        raise Revert(f"call data is short of argument {index}")
    if any(chunk[:12]):
        raise Revert(f"argument {index} is not a left-padded address")

    return "0x" + chunk[12:].hex()


@dataclass
class ScriptedToken:
    """A token's state plus the deviations it was scripted to exhibit."""

    decimals: int = 18
    balances: dict[str, int] = field(default_factory=dict)
    allowances: dict[tuple[str, str], int] = field(default_factory=dict)
    silent: bool = False
    fee_bps: int = 0
    zero_first_allowance: bool = False

    def balance_of(self, account: str) -> int:
        return self.balances.get(normalize_address(account), 0)

    def allowance(self, owner: str, spender: str) -> int:
        return self.allowances.get(
            (normalize_address(owner), normalize_address(spender)), 0
        )


@dataclass(frozen=True)
class Receipt:
    status: int
    return_data: bytes


@dataclass(frozen=True)
class Block:
    base_fee_per_gas: int
    reward: int


def _returned(token: ScriptedToken) -> bytes:
    return b"" if token.silent else _word(1)


def _move(token: ScriptedToken, sender: str, recipient: str, amount: int) -> None:
    balance = token.balance_of(sender)
    if balance < amount:
        raise Revert(f"transfer amount {amount} exceeds balance {balance}")

    fee = amount * token.fee_bps // BPS_PER_UNIT
    token.balances[sender] = balance - amount
    token.balances[recipient] = token.balance_of(recipient) + amount - fee


def _execute(token: ScriptedToken, data: bytes, caller: str) -> bytes:
    selector, args = data[:4], data[4:]

    if selector == DECIMALS_SELECTOR:
        return _word(token.decimals)

    if selector == BALANCE_OF_SELECTOR:
        return _word(token.balance_of(_address(args, 0)))

    if selector == ALLOWANCE_SELECTOR:
        return _word(token.allowance(_address(args, 0), _address(args, 1)))

    if selector == APPROVE_SELECTOR:
        spender = _address(args, 0)
        amount = _uint(args, 1)
        if token.zero_first_allowance and amount and token.allowance(caller, spender):
            raise Revert("approve moves a non-zero allowance to a non-zero value")
        token.allowances[(caller, spender)] = amount
        return _returned(token)

    if selector == TRANSFER_SELECTOR:
        _move(token, caller, _address(args, 0), _uint(args, 1))
        return _returned(token)

    if selector == TRANSFER_FROM_SELECTOR:
        owner = _address(args, 0)
        recipient = _address(args, 1)
        amount = _uint(args, 2)
        allowed = token.allowance(owner, caller)
        if allowed < amount:
            raise Revert(f"transfer amount {amount} exceeds allowance {allowed}")
        if allowed != UINT256_MAX:
            token.allowances[(owner, caller)] = allowed - amount
        _move(token, owner, recipient, amount)
        return _returned(token)

    raise Revert(f"function selector 0x{selector.hex()} is not implemented")


class ScriptedChain:
    """The three node methods this package talks to, over scripted state."""

    def __init__(
        self,
        tokens: dict[str, ScriptedToken],
        *,
        blocks: tuple[Block, ...] = (),
        pending_base_fee: int = 0,
    ) -> None:
        self._tokens = {normalize_address(at): token for at, token in tokens.items()}
        self.blocks = list(blocks)
        self.pending_base_fee = pending_base_fee
        self.calls: list[tuple[str, bytes]] = []
        self.sent: list[dict[str, object]] = []

    def token(self, address: str) -> ScriptedToken:
        try:
            return self._tokens[normalize_address(address)]
        except KeyError:
            raise Revert(f"no contract at {address}") from None

    def eth_call(self, to: str, data: bytes, *, sender: str = ALICE) -> bytes:
        """Run call data against a copy of state, returning what it would return."""
        self.calls.append((normalize_address(to), bytes(data)))

        return _execute(
            copy.deepcopy(self.token(to)), bytes(data), normalize_address(sender)
        )

    def send(self, *, to: str, data: bytes, sender: str) -> Receipt:
        """Broadcast call data as a transaction and keep what it changed."""
        token = self.token(to)
        working = copy.deepcopy(token)
        return_data = _execute(working, bytes(data), normalize_address(sender))

        # Committed only once the call ran to the end: a revert changes nothing.
        token.balances = working.balances
        token.allowances = working.allowances
        self.sent.append(
            {
                "to": normalize_address(to),
                "from": normalize_address(sender),
                "data": bytes(data),
            }
        )

        return Receipt(status=1, return_data=return_data)

    def eth_fee_history(
        self,
        block_count: int,
        newest_block: str = "latest",
        reward_percentiles: tuple[int, ...] = (50,),
    ) -> dict[str, object]:
        if newest_block != "latest":
            raise ValueError("the double only knows the latest block")
        if block_count < 1:
            raise ValueError(f"block_count must be at least 1, got {block_count}")

        window = self.blocks[-block_count:]

        return {
            "oldestBlock": len(self.blocks) - len(window),
            "baseFeePerGas": [block.base_fee_per_gas for block in window]
            + [self.pending_base_fee],
            "reward": [[block.reward] * len(reward_percentiles) for block in window],
        }


@dataclass(frozen=True)
class Accounts:
    alice: str = ALICE
    bob: str = BOB
    carol: str = CAROL


@dataclass(frozen=True)
class Tokens:
    dai: str = DAI
    usdt: str = USDT
    fee: str = FEE_TOKEN


@pytest.fixture
def accounts() -> Accounts:
    return Accounts()


@pytest.fixture
def tokens() -> Tokens:
    return Tokens()


@pytest.fixture
def chain() -> ScriptedChain:
    return ScriptedChain(
        {
            DAI: ScriptedToken(decimals=18, balances={ALICE: 1_000 * 10**18}),
            USDT: ScriptedToken(
                decimals=6,
                balances={ALICE: 1_000 * 10**6},
                silent=True,
                zero_first_allowance=True,
            ),
            FEE_TOKEN: ScriptedToken(
                decimals=18, balances={ALICE: 1_000 * 10**18}, fee_bps=250
            ),
        },
        blocks=(
            Block(base_fee_per_gas=21_000_000_000, reward=1_000_000_000),
            Block(base_fee_per_gas=22_400_000_000, reward=1_200_000_000),
            Block(base_fee_per_gas=23_100_000_000, reward=1_100_000_000),
            Block(base_fee_per_gas=24_000_000_000, reward=2_000_000_000),
            Block(base_fee_per_gas=23_800_000_000, reward=1_100_000_000),
        ),
        pending_base_fee=24_310_000_000,
    )
