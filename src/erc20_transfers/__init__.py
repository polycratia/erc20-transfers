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
from erc20_transfers.allowance import (
    UNLIMITED_ALLOWANCE,
    AllowanceCheck,
    InsufficientAllowance,
    check_allowance,
    encode_checked_transfer_from,
    require_allowance,
)

__all__ = [
    "ALLOWANCE_SELECTOR",
    "AllowanceCheck",
    "BALANCE_OF_SELECTOR",
    "InsufficientAllowance",
    "TRANSFER_FROM_SELECTOR",
    "TRANSFER_SELECTOR",
    "UINT256_MAX",
    "UNLIMITED_ALLOWANCE",
    "__version__",
    "check_allowance",
    "decode_bool",
    "decode_transfer_result",
    "decode_uint256",
    "encode_address",
    "encode_allowance",
    "encode_balance_of",
    "encode_checked_transfer_from",
    "encode_transfer",
    "encode_transfer_from",
    "encode_uint256",
    "normalize_address",
    "require_allowance",
]

__version__ = "0.1.0"
