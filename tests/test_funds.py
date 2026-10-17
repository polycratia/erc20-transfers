"""The two reverts a transferFrom can have, told apart before anything is sent.

A short balance and a short allowance revert the same call, so each side is
taken short on its own here, then both at once, then neither.
"""

import pytest

from erc20_transfers import (
    UNLIMITED_ALLOWANCE,
    InsufficientAllowance,
    InsufficientBalance,
    check_funds,
    encode_checked_transfer_from,
    encode_transfer_from,
    require_funds,
)

ALICE = "0x1111111111111111111111111111111111111111"
BOB = "0x2222222222222222222222222222222222222222"
CAROL = "0x3333333333333333333333333333333333333333"

AMOUNT = 1_500_000


def funds(*, allowance: int, balance: int):
    return check_funds(
        owner=ALICE,
        spender=BOB,
        required=AMOUNT,
        allowance=allowance,
        balance=balance,
    )


def test_neither_side_short_is_covered_and_encodes():
    check = funds(allowance=AMOUNT, balance=AMOUNT)

    assert check.sufficient
    assert check.short_side is None
    assert check.allowance_shortfall == 0
    assert check.balance_shortfall == 0
    assert "covered on both sides" in check.explain()
    check.raise_for_funds()

    assert encode_checked_transfer_from(
        sender=ALICE,
        spender=BOB,
        to=CAROL,
        amount=AMOUNT,
        allowance=AMOUNT,
        balance=AMOUNT,
    ) == encode_transfer_from(sender=ALICE, to=CAROL, amount=AMOUNT)


def test_a_short_allowance_names_the_allowance():
    check = funds(allowance=400_000, balance=AMOUNT)

    assert check.short_side == "allowance"
    assert check.balance_sufficient and not check.allowance_sufficient
    assert check.allowance_shortfall == 1_100_000
    assert check.allowance_check.shortfall == 1_100_000
    assert "reverts on the allowance, not on the balance" in check.explain()

    with pytest.raises(InsufficientAllowance) as raised:
        check.raise_for_funds()
    assert raised.value.check is check


def test_a_short_balance_names_the_balance():
    check = funds(allowance=AMOUNT, balance=400_000)

    assert check.short_side == "balance"
    assert check.allowance_sufficient and not check.balance_sufficient
    assert check.balance_shortfall == 1_100_000
    assert "reverts on the balance, not on the allowance" in check.explain()

    with pytest.raises(InsufficientBalance) as raised:
        check.raise_for_funds()
    assert raised.value.check.balance == 400_000


def test_both_sides_short_is_raised_as_the_balance():
    check = funds(allowance=400_000, balance=100_000)

    assert check.short_side == "both"
    assert check.allowance_shortfall == 1_100_000
    assert check.balance_shortfall == 1_400_000
    assert "short on both sides" in check.explain()

    with pytest.raises(InsufficientBalance):
        check.raise_for_funds()


def test_the_two_reverts_are_not_the_same_exception():
    assert not issubclass(InsufficientBalance, InsufficientAllowance)
    assert not issubclass(InsufficientAllowance, InsufficientBalance)

    with pytest.raises(InsufficientBalance):
        require_funds(
            owner=ALICE, spender=BOB, required=AMOUNT, allowance=AMOUNT, balance=0
        )
    with pytest.raises(InsufficientAllowance):
        require_funds(
            owner=ALICE, spender=BOB, required=AMOUNT, allowance=0, balance=AMOUNT
        )


def test_an_unlimited_allowance_does_not_cover_a_missing_balance():
    check = funds(allowance=UNLIMITED_ALLOWANCE, balance=0)

    assert check.unlimited_allowance
    assert check.short_side == "balance"
    assert "an unlimited allowance" in check.explain()


def test_a_short_balance_produces_no_call_data():
    with pytest.raises(InsufficientBalance):
        encode_checked_transfer_from(
            sender=ALICE,
            spender=BOB,
            to=CAROL,
            amount=AMOUNT,
            allowance=AMOUNT,
            balance=0,
        )


def test_addresses_are_normalized_and_both_numbers_validated():
    assert check_funds(
        owner=ALICE.upper().replace("0X", "0x"),
        spender=BOB,
        required=1,
        allowance=1,
        balance=1,
    ).owner == ALICE

    with pytest.raises(ValueError):
        funds(allowance=1, balance=-1)
    with pytest.raises(TypeError):
        funds(allowance=True, balance=1)
