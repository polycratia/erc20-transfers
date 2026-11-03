"""The transaction a token call travels in: every field, and what it refuses.

The fee numbers below are derived from a fixed base fee and tip rather than
read off a fixture, so a change in the assembled dict shows up here as the
field that changed and not as arithmetic to redo.
"""

import pytest

from erc20_transfers import (
    DEFAULT_GAS_MARGIN_BPS,
    MAX_GAS_MARGIN_BPS,
    MIN_GAS_LIMIT,
    TRANSACTION_TYPE,
    ForeignCallData,
    build_token_transaction,
    encode_allowance,
    encode_approve,
    encode_transfer,
    encode_transfer_from,
    estimate_fees,
    gas_with_margin,
    gwei,
    priority_fee_from_history,
)

ALICE = "0x1111111111111111111111111111111111111111"
BOB = "0x2222222222222222222222222222222222222222"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDT_LOWER = USDT.lower()

AMOUNT = 1_500_000
GAS_LIMIT = 65_000

TRANSFER = encode_transfer(to=BOB, amount=AMOUNT)
TRANSFER_FROM = encode_transfer_from(sender=ALICE, to=BOB, amount=AMOUNT)
APPROVE = encode_approve(spender=BOB, amount=AMOUNT)

FEES = estimate_fees(
    base_fee_per_gas=gwei(24),
    priority_fee_per_gas=gwei("1.1"),
    max_fee_ceiling=gwei(60),
)


def build(**overrides):
    arguments = {
        "chain_id": 1,
        "nonce": 7,
        "token": USDT,
        "data": TRANSFER,
        "fees": FEES,
        "gas_limit": GAS_LIMIT,
    }

    return build_token_transaction(**(arguments | overrides))


def test_every_field_of_the_type_2_transaction():
    transaction = build()

    assert transaction == {
        "type": 2,
        "chainId": 1,
        "nonce": 7,
        "to": USDT_LOWER,
        "value": 0,
        "data": TRANSFER,
        "gas": 65_000,
        "maxFeePerGas": 31_475_000_000,
        "maxPriorityFeePerGas": 1_100_000_000,
    }
    assert transaction["type"] == TRANSACTION_TYPE
    assert transaction["maxFeePerGas"] == FEES.max_fee_per_gas


def test_the_value_is_pinned_to_zero_and_cannot_be_given():
    assert build()["value"] == 0

    with pytest.raises(TypeError):
        build_token_transaction(
            chain_id=1,
            nonce=7,
            token=USDT,
            data=TRANSFER,
            fees=FEES,
            gas_limit=GAS_LIMIT,
            value=1,
        )


def test_the_three_token_calls_are_the_ones_accepted():
    for data in (TRANSFER, TRANSFER_FROM, APPROVE):
        assert build(data=data)["data"] == data


def test_call_data_for_another_function_is_refused():
    with pytest.raises(ForeignCallData):
        build(data=encode_allowance(owner=ALICE, spender=BOB))
    with pytest.raises(ForeignCallData):
        build(data=b"")
    with pytest.raises(ForeignCallData):
        build(data=bytes.fromhex("deadbeef") + TRANSFER[4:])


def test_token_call_data_of_the_wrong_length_is_refused():
    with pytest.raises(ForeignCallData):
        build(data=TRANSFER[:4])
    with pytest.raises(ForeignCallData):
        build(data=TRANSFER + b"\x00" * 32)
    with pytest.raises(ForeignCallData):
        build(data=TRANSFER_FROM[: len(TRANSFER)])
    with pytest.raises(TypeError):
        build(data="0x" + TRANSFER.hex())


def test_the_chain_id_is_required_and_never_zero():
    assert build(chain_id=137)["chainId"] == 137

    with pytest.raises(TypeError):
        build_token_transaction(
            nonce=7, token=USDT, data=TRANSFER, fees=FEES, gas_limit=GAS_LIMIT
        )
    with pytest.raises(ValueError):
        build(chain_id=0)
    with pytest.raises(ValueError):
        build(chain_id=-1)
    with pytest.raises(TypeError):
        build(chain_id=True)


def test_the_remaining_fields_are_validated():
    assert build(nonce=0)["nonce"] == 0

    with pytest.raises(ValueError):
        build(nonce=-1)
    with pytest.raises(TypeError):
        build(nonce=True)
    with pytest.raises(ValueError):
        build(gas_limit=MIN_GAS_LIMIT - 1)
    with pytest.raises(ValueError):
        build(token="0x1234")
    with pytest.raises(TypeError):
        build(fees=FEES.as_transaction_fields())


def test_the_margin_is_bps_over_the_estimate_rounded_up():
    assert gas_with_margin(65_000, 2_500) == 81_250
    assert gas_with_margin(65_000) == gas_with_margin(65_000, DEFAULT_GAS_MARGIN_BPS)
    assert gas_with_margin(65_000, 0) == 65_000
    assert gas_with_margin(65_000, MAX_GAS_MARGIN_BPS) == 130_000
    # 21001 at one basis point is 2.1001 units of margin, so three are added.
    assert gas_with_margin(21_001, 1) == 21_004


def test_the_estimate_and_the_margin_are_bounded():
    with pytest.raises(ValueError):
        gas_with_margin(65_000, -1)
    with pytest.raises(ValueError):
        gas_with_margin(65_000, MAX_GAS_MARGIN_BPS + 1)
    with pytest.raises(TypeError):
        gas_with_margin(65_000, True)
    with pytest.raises(ValueError):
        gas_with_margin(MIN_GAS_LIMIT - 1)


def test_the_assembled_transaction_is_what_gets_broadcast(chain, accounts, tokens):
    history = chain.eth_fee_history(5, "latest", (50,))
    fees = estimate_fees(
        base_fee_per_gas=history["baseFeePerGas"][-1],
        priority_fee_per_gas=priority_fee_from_history(
            [block[0] for block in history["reward"]]
        ),
        max_fee_ceiling=gwei(60),
    )
    data = encode_transfer(to=accounts.bob, amount=AMOUNT)

    transaction = build_token_transaction(
        chain_id=1,
        nonce=0,
        token=tokens.usdt,
        data=data,
        fees=fees,
        gas_limit=gas_with_margin(55_000),
    )
    chain.send(
        to=transaction["to"], data=transaction["data"], sender=accounts.alice
    )

    assert transaction["value"] == 0
    assert transaction["gas"] == 68_750
    assert transaction["maxFeePerGas"] == fees.max_fee_per_gas
    assert chain.sent[-1]["data"] == data
