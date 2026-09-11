"""Known values for everything that encodes, decodes or converts.

Selectors, argument order, padding side and byte order are fixed by the ABI and
by the standard; a change in any of them is a change in what the chain is asked
to do, so they are pinned to literals here rather than derived.
"""

from decimal import Decimal

import pytest

from erc20_transfers import (
    ALLOWANCE_SELECTOR,
    APPROVE_SELECTOR,
    BALANCE_OF_SELECTOR,
    DECIMALS_SELECTOR,
    TRANSFER_FROM_SELECTOR,
    TRANSFER_SELECTOR,
    UINT256_MAX,
    UNLIMITED_ALLOWANCE,
    InsufficientAllowance,
    TokenAmount,
    base_fee_headroom,
    check_allowance,
    decode_bool,
    decode_decimals,
    decode_transfer_result,
    decode_uint256,
    encode_allowance,
    encode_approve,
    encode_balance_of,
    encode_checked_transfer_from,
    encode_decimals,
    encode_transfer,
    encode_transfer_from,
    from_units,
    gwei,
    next_base_fee,
    normalize_address,
    priority_fee_from_history,
    to_units,
)

ALICE = "0x1111111111111111111111111111111111111111"
BOB = "0x2222222222222222222222222222222222222222"
CAROL = "0x3333333333333333333333333333333333333333"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDT_LOWER = "0xdac17f958d2ee523a2206206994597c13d831ec7"

GAS_LIMIT = 30_000_000


def word_hex(hexdigits: str) -> str:
    """One ABI word as hex: right-aligned in 32 bytes, as the encoding requires."""
    return hexdigits.rjust(64, "0")


def word_bytes(value: int) -> bytes:
    return value.to_bytes(32, "big")


def test_selectors_are_the_ones_the_standard_fixes():
    assert TRANSFER_SELECTOR.hex() == "a9059cbb"
    assert TRANSFER_FROM_SELECTOR.hex() == "23b872dd"
    assert BALANCE_OF_SELECTOR.hex() == "70a08231"
    assert ALLOWANCE_SELECTOR.hex() == "dd62ed3e"
    assert APPROVE_SELECTOR.hex() == "095ea7b3"
    assert DECIMALS_SELECTOR.hex() == "313ce567"


def test_transfer_call_data():
    assert encode_transfer(to=BOB, amount=1_500_000).hex() == (
        "a9059cbb" + word_hex("2" * 40) + word_hex("16e360")
    )


def test_transfer_from_encodes_owner_then_recipient():
    assert encode_transfer_from(sender=ALICE, to=CAROL, amount=1).hex() == (
        "23b872dd" + word_hex("1" * 40) + word_hex("3" * 40) + word_hex("1")
    )


def test_read_call_data():
    assert encode_balance_of(account=ALICE).hex() == "70a08231" + word_hex("1" * 40)
    assert encode_allowance(owner=ALICE, spender=BOB).hex() == (
        "dd62ed3e" + word_hex("1" * 40) + word_hex("2" * 40)
    )
    assert encode_decimals().hex() == "313ce567"


def test_approve_call_data_including_the_unlimited_value():
    assert encode_approve(spender=BOB, amount=0).hex() == (
        "095ea7b3" + word_hex("2" * 40) + word_hex("")
    )
    assert encode_approve(spender=BOB, amount=UNLIMITED_ALLOWANCE).hex() == (
        "095ea7b3" + word_hex("2" * 40) + "f" * 64
    )


def test_addresses_are_lowercased_not_checksummed():
    assert normalize_address(USDT) == USDT_LOWER
    assert normalize_address(USDT_LOWER[2:]) == USDT_LOWER
    assert encode_balance_of(account=USDT).hex() == (
        "70a08231" + word_hex(USDT_LOWER[2:])
    )

    with pytest.raises(ValueError):
        normalize_address("0x1234")
    with pytest.raises(ValueError):
        normalize_address("0x" + "z" * 40)
    with pytest.raises(TypeError):
        normalize_address(b"\x11" * 20)


def test_amount_words_are_bounded():
    with pytest.raises(ValueError):
        encode_transfer(to=BOB, amount=-1)
    with pytest.raises(ValueError):
        encode_transfer(to=BOB, amount=UINT256_MAX + 1)
    with pytest.raises(TypeError):
        encode_transfer(to=BOB, amount=True)


def test_return_data_vectors():
    assert decode_uint256(word_bytes(1_500_000)) == 1_500_000
    assert decode_uint256(b"\xff" * 32) == UINT256_MAX
    assert decode_bool(word_bytes(1)) is True
    assert decode_bool(word_bytes(0)) is False
    assert decode_transfer_result(b"") is True

    with pytest.raises(ValueError):
        decode_uint256(b"\x00" * 31)
    with pytest.raises(ValueError):
        decode_bool(word_bytes(2))


def test_decimals_vectors():
    assert decode_decimals(word_bytes(6)) == 6
    assert decode_decimals(word_bytes(0)) == 0
    assert decode_decimals(word_bytes(255)) == 255

    with pytest.raises(ValueError):
        decode_decimals(word_bytes(256))


def test_unit_conversion_vectors():
    assert to_units(Decimal("1.5"), decimals=6) == 1_500_000
    assert to_units(Decimal("1.5"), decimals=18) == 1_500_000_000_000_000_000
    assert to_units("0.00000001", decimals=8) == 1
    assert to_units(1, decimals=0) == 1
    assert from_units(1_500_000, decimals=6) == Decimal("1.5")
    assert str(TokenAmount(units=1_500_000, decimals=6)) == "1.500000"
    assert TokenAmount.from_return_data(
        word_bytes(1_500_000), decimals=6
    ).amount == Decimal("1.5")


def test_precision_the_token_cannot_hold_is_refused():
    with pytest.raises(ValueError):
        to_units(Decimal("1.0000005"), decimals=6)
    with pytest.raises(TypeError):
        to_units(1.5, decimals=6)
    with pytest.raises(ValueError):
        to_units(Decimal("-1"), decimals=6)


def test_gwei_vectors():
    assert gwei(60) == 60_000_000_000
    assert gwei(Decimal("1.5")) == 1_500_000_000
    assert gwei("0.000000001") == 1

    with pytest.raises(ValueError):
        gwei(Decimal("0.0000000001"))


def test_next_base_fee_vectors():
    base = gwei(100)
    target = GAS_LIMIT // 2

    assert (
        next_base_fee(base_fee_per_gas=base, gas_used=target, gas_limit=GAS_LIMIT)
        == base
    )
    assert (
        next_base_fee(base_fee_per_gas=base, gas_used=GAS_LIMIT, gas_limit=GAS_LIMIT)
        == 112_500_000_000
    )
    assert (
        next_base_fee(base_fee_per_gas=base, gas_used=0, gas_limit=GAS_LIMIT)
        == 87_500_000_000
    )
    assert (
        next_base_fee(base_fee_per_gas=base, gas_used=target + 1, gas_limit=GAS_LIMIT)
        == base + 833
    )
    # A block over target always moves the fee, even when the delta rounds to 0.
    assert (
        next_base_fee(base_fee_per_gas=7, gas_used=target + 1, gas_limit=GAS_LIMIT) == 8
    )


def test_base_fee_headroom_vectors():
    assert base_fee_headroom(gwei(100), blocks=0) == gwei(100)
    assert base_fee_headroom(gwei(100), blocks=1) == 112_500_000_000
    assert base_fee_headroom(gwei(100), blocks=2) == 126_562_500_000

    with pytest.raises(ValueError):
        base_fee_headroom(gwei(100), blocks=65)


def test_priority_fee_is_the_median_not_the_mean():
    rewards = [gwei(1), gwei("1.2"), gwei("1.1"), gwei(2), gwei("1.1")]

    assert priority_fee_from_history(rewards) == gwei("1.1")
    assert priority_fee_from_history([gwei(1), gwei(2)]) == gwei("1.5")

    with pytest.raises(ValueError):
        priority_fee_from_history([])


def test_a_short_allowance_is_explained_with_its_numbers():
    check = check_allowance(
        owner=ALICE, spender=BOB, required=1_500_000, current=400_000
    )

    assert not check.sufficient
    assert check.shortfall == 1_100_000
    assert check.explain() == (
        f"{BOB} holds an allowance of 400000 from {ALICE}, but transferFrom of "
        f"1500000 needs 1100000 more; the call reverts until the owner approves "
        f"at least 1500000"
    )

    with pytest.raises(InsufficientAllowance) as raised:
        encode_checked_transfer_from(
            sender=ALICE, spender=BOB, to=CAROL, amount=1_500_000, allowance=400_000
        )
    assert raised.value.check.shortfall == 1_100_000


def test_an_unlimited_allowance_covers_anything():
    check = check_allowance(
        owner=ALICE, spender=BOB, required=UINT256_MAX, current=UNLIMITED_ALLOWANCE
    )

    assert check.unlimited
    assert check.sufficient
    assert "an unlimited allowance" in check.explain()
