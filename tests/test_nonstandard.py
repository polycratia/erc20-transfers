from decimal import Decimal

import pytest

from erc20_transfers import (
    APPROVE_SELECTOR,
    FeeOnTransfer,
    NonBooleanReturn,
    TransferRejected,
    encode_approve,
    measure_received,
    plan_approval,
    read_transfer_return,
    requires_zero_first_allowance,
)

BOB = "0x2222222222222222222222222222222222222222"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"


def word(value: int) -> bytes:
    return value.to_bytes(32, "big")


def test_silent_token_counts_as_success():
    result = read_transfer_return(b"")

    assert result.silent
    assert result.succeeded
    result.raise_for_status()


def test_boolean_return_is_decoded():
    assert read_transfer_return(word(1)).succeeded
    assert not read_transfer_return(word(1)).silent
    assert not read_transfer_return(word(0)).succeeded


def test_false_return_raises():
    with pytest.raises(TransferRejected):
        read_transfer_return(word(0)).raise_for_status()


def test_unreadable_return_is_refused():
    with pytest.raises(NonBooleanReturn):
        read_transfer_return(word(2))
    with pytest.raises(NonBooleanReturn):
        read_transfer_return(b"\x00" * 31)


def test_fee_is_measured_from_balances():
    receipt = measure_received(sent=1_000_000, balance_before=0, balance_after=950_000)

    assert receipt.received == 950_000
    assert receipt.fee == 50_000
    assert receipt.has_fee
    assert receipt.fee_bps == Decimal(500)
    with pytest.raises(FeeOnTransfer):
        receipt.raise_for_fee()


def test_transfer_without_fee_passes():
    receipt = measure_received(
        sent=1_000_000, balance_before=25, balance_after=1_000_025
    )

    assert not receipt.has_fee
    assert receipt.fee == 0
    receipt.raise_for_fee()


def test_impossible_balances_are_refused():
    with pytest.raises(ValueError):
        measure_received(sent=10, balance_before=100, balance_after=90)
    with pytest.raises(ValueError):
        measure_received(sent=10, balance_before=0, balance_after=11)


def test_first_approval_is_one_transaction():
    plan = plan_approval(spender=BOB, current=0, required=1_500_000)

    assert not plan.resets_first
    assert [step.value for step in plan.steps] == [1_500_000]
    assert plan.call_data() == (encode_approve(spender=BOB, amount=1_500_000),)


def test_raising_a_non_zero_allowance_goes_through_zero():
    plan = plan_approval(spender=BOB, current=400_000, required=1_500_000)

    assert plan.resets_first
    assert [step.value for step in plan.steps] == [0, 1_500_000]
    assert plan.steps[0].data == encode_approve(spender=BOB, amount=0)


def test_reset_can_be_skipped():
    plan = plan_approval(
        spender=BOB, current=400_000, required=1_500_000, reset_first=False
    )

    assert [step.value for step in plan.steps] == [1_500_000]


def test_sufficient_allowance_needs_no_approve():
    plan = plan_approval(spender=BOB, current=1_500_000, required=1_500_000)

    assert not plan.needed
    assert plan.steps == ()


def test_revoking_is_one_approve_of_zero():
    plan = plan_approval(spender=BOB, current=400_000, required=0)

    assert [step.value for step in plan.steps] == [0]
    assert not plan_approval(spender=BOB, current=0, required=0).needed


def test_spender_is_normalized_and_amounts_validated():
    assert plan_approval(spender=USDT, current=0, required=1).spender == USDT.lower()

    with pytest.raises(ValueError):
        plan_approval(spender=BOB, current=0, required=-1)
    with pytest.raises(TypeError):
        plan_approval(spender=BOB, current=0, required=True)


def test_zero_first_registry_matches_any_case():
    assert requires_zero_first_allowance(USDT)
    assert not requires_zero_first_allowance(BOB)


def test_approve_call_data():
    data = encode_approve(spender=BOB, amount=0)

    assert data[:4] == APPROVE_SELECTOR
    assert len(data) == 68
