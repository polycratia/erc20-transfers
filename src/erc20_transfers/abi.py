"""ABI encoding for the minimal ERC-20 surface: two transfer paths, two reads.

Four functions do not justify shipping a full ABI JSON file plus a parser for
it. A selector is the first four bytes of keccak-256 over the canonical
signature; for these functions the values are fixed by the standard, so they
are spelled out next to the signature they come from and the package stays
free of a keccak dependency.

Addresses are accepted in any case and normalised to lowercase. EIP-55
checksums are *not* verified, because that also needs keccak-256.
"""

from __future__ import annotations

from typing import Final

WORD_SIZE: Final = 32
UINT256_MAX: Final = 2**256 - 1

TRANSFER_SELECTOR: Final = bytes.fromhex("a9059cbb")  # transfer(address,uint256)
TRANSFER_FROM_SELECTOR: Final = bytes.fromhex("23b872dd")  # transferFrom(address,address,uint256)
BALANCE_OF_SELECTOR: Final = bytes.fromhex("70a08231")  # balanceOf(address)
ALLOWANCE_SELECTOR: Final = bytes.fromhex("dd62ed3e")  # allowance(address,address)


def normalize_address(value: str) -> str:
    """Return `value` as a lowercase 0x-prefixed 20-byte address."""
    if not isinstance(value, str):
        raise TypeError(f"address must be a string, got {type(value).__name__}")

    digits = value.strip()
    if digits[:2].lower() == "0x":
        digits = digits[2:]
    if len(digits) != 40:
        raise ValueError(
            f"address must be 40 hex digits, got {len(digits)}: {value!r}"
        )
    try:
        raw = bytes.fromhex(digits)
    except ValueError as exc:
        raise ValueError(f"address is not valid hex: {value!r}") from exc

    return "0x" + raw.hex()


def encode_address(value: str) -> bytes:
    """Encode an address as one 32-byte word, left-padded with zeros."""
    raw = bytes.fromhex(normalize_address(value)[2:])
    return raw.rjust(WORD_SIZE, b"\x00")


def encode_uint256(value: int) -> bytes:
    """Encode a non-negative integer as one 32-byte word."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"amount must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"amount must not be negative, got {value}")
    if value > UINT256_MAX:
        raise ValueError(f"amount exceeds uint256 range: {value}")

    return value.to_bytes(WORD_SIZE, "big")


def encode_transfer(*, to: str, amount: int) -> bytes:
    """Call data for `transfer(to, amount)`."""
    return TRANSFER_SELECTOR + encode_address(to) + encode_uint256(amount)


def encode_transfer_from(*, sender: str, to: str, amount: int) -> bytes:
    """Call data for `transferFrom(sender, to, amount)`.

    `sender` is the account the tokens leave; it must have granted an
    allowance to whoever signs the transaction.
    """
    return (
        TRANSFER_FROM_SELECTOR
        + encode_address(sender)
        + encode_address(to)
        + encode_uint256(amount)
    )


def encode_balance_of(*, account: str) -> bytes:
    """Call data for the `balanceOf(account)` read."""
    return BALANCE_OF_SELECTOR + encode_address(account)


def encode_allowance(*, owner: str, spender: str) -> bytes:
    """Call data for the `allowance(owner, spender)` read."""
    return ALLOWANCE_SELECTOR + encode_address(owner) + encode_address(spender)


def decode_uint256(data: bytes) -> int:
    """Decode the return data of `balanceOf` or `allowance`."""
    raw = bytes(data)
    if len(raw) != WORD_SIZE:
        raise ValueError(f"expected {WORD_SIZE} bytes of return data, got {len(raw)}")

    return int.from_bytes(raw, "big")


def decode_bool(data: bytes) -> bool:
    """Decode a single ABI-encoded boolean word."""
    value = decode_uint256(data)
    if value > 1:
        raise ValueError(f"return data is not a boolean: 0x{bytes(data).hex()}")

    return value == 1


def decode_transfer_result(data: bytes) -> bool:
    """Decode what a `transfer` or `transferFrom` gave back.

    Tokens that predate the finalised standard (USDT among them) return
    nothing at all; a call that did not revert is a success for those.
    """
    if not data:
        return True

    return decode_bool(data)
