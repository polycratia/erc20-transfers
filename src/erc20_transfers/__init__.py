"""ERC-20 transfers with explicit allowances, gas strategy and broadcasting."""

from erc20_transfers.abi import (
    ALLOWANCE_SELECTOR,
    BALANCE_OF_SELECTOR,
    TRANSFER_FROM_SELECTOR,
    TRANSFER_SELECTOR,
    UINT256_MAX,
    decode_bool,
    decode_transfer_result,
    decode_uint256,
    encode_address,
    encode_allowance,
    encode_balance_of,
    encode_transfer,
    encode_transfer_from,
    encode_uint256,
    normalize_address,
)

__all__ = [
    "ALLOWANCE_SELECTOR",
    "BALANCE_OF_SELECTOR",
    "TRANSFER_FROM_SELECTOR",
    "TRANSFER_SELECTOR",
    "UINT256_MAX",
    "__version__",
    "decode_bool",
    "decode_transfer_result",
    "decode_uint256",
    "encode_address",
    "encode_allowance",
    "encode_balance_of",
    "encode_transfer",
    "encode_transfer_from",
    "encode_uint256",
    "normalize_address",
]

__version__ = "0.1.0"
