"""The type-2 transaction a token call travels in, assembled field by field.

A token transfer is a contract call, not a payment. Balances live in the
contract's storage, so nothing about the transfer depends on the ether the
transaction carries - and ether sent along with it is at best stuck in a
contract that never looks at it. `value` is therefore not an argument here: it
is 0 in the result and cannot be anything else.

`chainId` is required for the opposite reason. Since EIP-155 it is part of
what gets signed, which is what stops a transaction signed for one chain from
being replayed on another; omitted, the signature is good everywhere the
sender has that nonce. And the call data is checked against the three
selectors this package encodes, because a transaction assembled here is one
that moves tokens rather than one that happens to be addressed to a token.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from erc20_transfers.abi import (
    TRANSFER_FROM_SELECTOR,
    TRANSFER_SELECTOR,
    UINT256_MAX,
    WORD_SIZE,
    normalize_address,
)
from erc20_transfers.gas import Eip1559Fees
from erc20_transfers.nonstandard import APPROVE_SELECTOR

__all__ = [
    "DEFAULT_GAS_MARGIN_BPS",
    "ForeignCallData",
    "MAX_GAS_MARGIN_BPS",
    "MIN_GAS_LIMIT",
    "TOKEN_SELECTORS",
    "TRANSACTION_TYPE",
    "build_token_transaction",
    "gas_with_margin",
]

# EIP-1559 transactions are type 2; the two fee fields only mean something
# under that envelope.
TRANSACTION_TYPE: Final = 2

SELECTOR_SIZE: Final = 4
_BPS_PER_UNIT: Final = 10_000

# The intrinsic cost of any transaction, charged before a single opcode runs.
MIN_GAS_LIMIT: Final = 21_000

DEFAULT_GAS_MARGIN_BPS: Final = 2_500
MAX_GAS_MARGIN_BPS: Final = 10_000

# The only calls this package encodes, and so the only ones it can assemble a
# transaction for.
TOKEN_SELECTORS: Final[Mapping[bytes, str]] = MappingProxyType(
    {
        TRANSFER_SELECTOR: "transfer(address,uint256)",
        TRANSFER_FROM_SELECTOR: "transferFrom(address,address,uint256)",
        APPROVE_SELECTOR: "approve(address,uint256)",
    }
)


class ForeignCallData(ValueError):
    """Raised for call data that is not one of the three ERC-20 token calls."""


def _uint(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    if value > UINT256_MAX:
        raise ValueError(f"{name} exceeds uint256 range: {value}")

    return value


def _encoded_size(signature: str) -> int:
    arguments = signature[signature.index("(") + 1 : -1].split(",")

    return SELECTOR_SIZE + WORD_SIZE * len(arguments)


def _token_call_data(data: bytes) -> bytes:
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"data must be bytes, got {type(data).__name__}")

    raw = bytes(data)
    if not raw:
        raise ForeignCallData(
            "call data is empty; a transaction with no call data moves ether, and "
            "a token call moves none"
        )

    selector = raw[:SELECTOR_SIZE]
    signature = TOKEN_SELECTORS.get(selector)
    if signature is None:
        raise ForeignCallData(
            f"call data starting 0x{selector.hex()} is not a transfer, transferFrom "
            f"or approve; whatever that transaction does, this package cannot say "
            f"that it moves tokens"
        )

    expected = _encoded_size(signature)
    if len(raw) != expected:
        raise ForeignCallData(
            f"{signature} takes {expected} bytes of call data, got {len(raw)}: "
            f"0x{raw.hex()}"
        )

    return raw


def _chain_id(value: int) -> int:
    chain = _uint(value, "chain_id")
    if chain == 0:
        raise ValueError(
            "chain_id must not be 0; the chain id is signed into the transaction, "
            "and a signature made for no chain is good on every chain"
        )

    return chain


def _gas_limit(value: int) -> int:
    limit = _uint(value, "gas_limit")
    if limit < MIN_GAS_LIMIT:
        raise ValueError(
            f"gas_limit must be at least {MIN_GAS_LIMIT}, got {limit}; that is the "
            f"intrinsic cost of a transaction before any call runs"
        )

    return limit


def gas_with_margin(estimate: int, bps: int = DEFAULT_GAS_MARGIN_BPS) -> int:
    """Raise a gas estimate by `bps` basis points, rounding up.

    `eth_estimateGas` measures the call against current state, and a token
    call can cost more than that by the time it is mined: a transfer that
    writes a recipient's balance slot from zero pays for a new slot, and the
    state the estimate was taken over may not be the state the transaction
    lands in. Unused gas is refunded, while running out is a transaction that
    pays for every opcode it ran and moves nothing - so the limit is the
    estimate plus a margin.
    """
    gas = _uint(estimate, "estimate")
    margin = _uint(bps, "bps")
    if margin > MAX_GAS_MARGIN_BPS:
        raise ValueError(
            f"bps must be between 0 and {MAX_GAS_MARGIN_BPS}, got {margin}"
        )
    if gas < MIN_GAS_LIMIT:
        raise ValueError(
            f"estimate must be at least {MIN_GAS_LIMIT}, got {gas}; no transaction "
            f"costs less than the intrinsic gas"
        )

    return gas + (gas * margin + _BPS_PER_UNIT - 1) // _BPS_PER_UNIT


def build_token_transaction(
    *,
    chain_id: int,
    nonce: int,
    token: str,
    data: bytes,
    fees: Eip1559Fees,
    gas_limit: int,
) -> dict[str, int | str | bytes]:
    """The type-2 transaction fields for a token call, ready to be signed.

    `token` is the contract the call goes to, `data` the call data that
    `encode_transfer`, `encode_checked_transfer_from` or `encode_approve`
    produced, `fees` what `estimate_fees` returned and `gas_limit` a limit
    from `gas_with_margin`.

    There is no `value` argument: a token call moves no ether, so the field
    is 0 and cannot be given another number. `chain_id` has no default
    because it is signed into the transaction. Call data for anything but
    `transfer`, `transferFrom` or `approve`, or of the wrong length for the
    one it names, raises `ForeignCallData`.
    """
    if not isinstance(fees, Eip1559Fees):
        raise TypeError(
            f"fees must be an Eip1559Fees, got {type(fees).__name__}; estimate_fees "
            f"returns one"
        )

    return {
        "type": TRANSACTION_TYPE,
        "chainId": _chain_id(chain_id),
        "nonce": _uint(nonce, "nonce"),
        "to": normalize_address(token),
        "value": 0,
        "data": _token_call_data(data),
        "gas": _gas_limit(gas_limit),
        **fees.as_transaction_fields(),
    }
