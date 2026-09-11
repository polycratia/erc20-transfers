"""Behaviour against the scripted chain: what the package encodes, broadcast.

Every amount here is read off the double rather than assumed and every
transaction goes through `send`, so these tests fail the way a chain would: a
revert where a contract would revert, a balance that did not move where nothing
moved.
"""

from decimal import Decimal

import pytest
from conftest import Revert

from erc20_transfers import (
    FeeCeilingTooLow,
    FeeOnTransfer,
    InsufficientAllowance,
    TokenAmount,
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
    estimate_fees,
    gwei,
    measure_received,
    plan_approval,
    priority_fee_from_history,
    read_transfer_return,
    requires_zero_first_allowance,
    to_units,
)


def balance_of(chain, token, account):
    return decode_uint256(chain.eth_call(token, encode_balance_of(account=account)))


def allowance_of(chain, token, owner, spender):
    return decode_uint256(
        chain.eth_call(token, encode_allowance(owner=owner, spender=spender))
    )


def fees_from(chain, *, ceiling):
    history = chain.eth_fee_history(5, "latest", (50,))

    return estimate_fees(
        base_fee_per_gas=history["baseFeePerGas"][-1],
        priority_fee_per_gas=priority_fee_from_history(
            [block[0] for block in history["reward"]]
        ),
        max_fee_ceiling=ceiling,
    )


def test_amounts_are_read_with_the_decimals_the_token_reports(chain, accounts, tokens):
    decimals = decode_decimals(chain.eth_call(tokens.usdt, encode_decimals()))
    balance = TokenAmount.from_return_data(
        chain.eth_call(tokens.usdt, encode_balance_of(account=accounts.alice)),
        decimals=decimals,
    )

    assert decimals == 6
    assert balance.units == 1_000_000_000
    assert balance.amount == Decimal(1_000)
    assert to_units(Decimal("1.5"), decimals=decimals) == 1_500_000


def test_a_transfer_moves_tokens_and_reports_true(chain, accounts, tokens):
    amount = to_units(Decimal("2.5"), decimals=18)

    receipt = chain.send(
        to=tokens.dai,
        sender=accounts.alice,
        data=encode_transfer(to=accounts.bob, amount=amount),
    )
    result = read_transfer_return(receipt.return_data)
    result.raise_for_status()

    assert receipt.status == 1
    assert result.succeeded and not result.silent
    assert balance_of(chain, tokens.dai, accounts.bob) == amount


def test_eth_call_of_a_transfer_moves_nothing(chain, accounts, tokens):
    data = encode_transfer(to=accounts.bob, amount=10**18)

    simulated = chain.eth_call(tokens.dai, data, sender=accounts.alice)

    assert decode_transfer_result(simulated) is True
    assert balance_of(chain, tokens.dai, accounts.bob) == 0
    assert chain.sent == []


def test_a_transfer_beyond_the_balance_reverts_and_leaves_state_alone(
    chain, accounts, tokens
):
    with pytest.raises(Revert):
        chain.send(
            to=tokens.dai,
            sender=accounts.bob,
            data=encode_transfer(to=accounts.carol, amount=1),
        )

    assert balance_of(chain, tokens.dai, accounts.carol) == 0
    assert balance_of(chain, tokens.dai, accounts.alice) == 1_000 * 10**18


def test_transfer_from_is_refused_before_the_chain_can_revert_it(
    chain, accounts, tokens
):
    allowance = allowance_of(chain, tokens.dai, accounts.alice, accounts.bob)
    assert allowance == 0

    with pytest.raises(InsufficientAllowance):
        encode_checked_transfer_from(
            sender=accounts.alice,
            spender=accounts.bob,
            to=accounts.carol,
            amount=10**18,
            allowance=allowance,
        )

    with pytest.raises(Revert):
        chain.send(
            to=tokens.dai,
            sender=accounts.bob,
            data=encode_transfer_from(
                sender=accounts.alice, to=accounts.carol, amount=10**18
            ),
        )


def test_an_approved_spender_moves_the_owners_tokens(chain, accounts, tokens):
    amount = to_units(Decimal("25"), decimals=18)
    plan = plan_approval(
        spender=accounts.bob,
        current=allowance_of(chain, tokens.dai, accounts.alice, accounts.bob),
        required=amount,
    )
    assert not plan.resets_first

    for step in plan.steps:
        chain.send(to=tokens.dai, sender=accounts.alice, data=step.data)

    allowance = allowance_of(chain, tokens.dai, accounts.alice, accounts.bob)
    assert allowance == amount

    data = encode_checked_transfer_from(
        sender=accounts.alice,
        spender=accounts.bob,
        to=accounts.carol,
        amount=amount,
        allowance=allowance,
    )
    receipt = chain.send(to=tokens.dai, sender=accounts.bob, data=data)

    assert read_transfer_return(receipt.return_data).succeeded
    assert balance_of(chain, tokens.dai, accounts.carol) == amount
    assert allowance_of(chain, tokens.dai, accounts.alice, accounts.bob) == 0


def test_raising_a_usdt_allowance_directly_reverts(chain, accounts, tokens):
    chain.send(
        to=tokens.usdt,
        sender=accounts.alice,
        data=encode_approve(spender=accounts.bob, amount=400_000),
    )

    with pytest.raises(Revert):
        chain.send(
            to=tokens.usdt,
            sender=accounts.alice,
            data=encode_approve(spender=accounts.bob, amount=1_500_000),
        )

    assert allowance_of(chain, tokens.usdt, accounts.alice, accounts.bob) == 400_000


def test_the_planned_detour_through_zero_gets_through(chain, accounts, tokens):
    assert requires_zero_first_allowance(tokens.usdt)
    chain.send(
        to=tokens.usdt,
        sender=accounts.alice,
        data=encode_approve(spender=accounts.bob, amount=400_000),
    )

    plan = plan_approval(
        spender=accounts.bob,
        current=allowance_of(chain, tokens.usdt, accounts.alice, accounts.bob),
        required=1_500_000,
    )
    assert plan.resets_first

    for step in plan.steps:
        chain.send(to=tokens.usdt, sender=accounts.alice, data=step.data)

    assert allowance_of(chain, tokens.usdt, accounts.alice, accounts.bob) == 1_500_000


def test_a_silent_token_reports_success_by_returning_nothing(chain, accounts, tokens):
    receipt = chain.send(
        to=tokens.usdt,
        sender=accounts.alice,
        data=encode_transfer(to=accounts.bob, amount=1_500_000),
    )
    result = read_transfer_return(receipt.return_data)

    assert receipt.return_data == b""
    assert result.silent and result.succeeded
    assert balance_of(chain, tokens.usdt, accounts.bob) == 1_500_000


def test_a_fee_token_delivers_less_than_it_was_sent(chain, accounts, tokens):
    before = balance_of(chain, tokens.fee, accounts.bob)
    chain.send(
        to=tokens.fee,
        sender=accounts.alice,
        data=encode_transfer(to=accounts.bob, amount=1_000_000),
    )
    after = balance_of(chain, tokens.fee, accounts.bob)

    receipt = measure_received(
        sent=1_000_000, balance_before=before, balance_after=after
    )

    assert receipt.received == 975_000
    assert receipt.fee == 25_000
    assert receipt.fee_bps == Decimal(250)
    with pytest.raises(FeeOnTransfer):
        receipt.raise_for_fee()


def test_a_plain_token_delivers_all_of_it(chain, accounts, tokens):
    before = balance_of(chain, tokens.dai, accounts.bob)
    chain.send(
        to=tokens.dai,
        sender=accounts.alice,
        data=encode_transfer(to=accounts.bob, amount=10**18),
    )
    after = balance_of(chain, tokens.dai, accounts.bob)

    receipt = measure_received(sent=10**18, balance_before=before, balance_after=after)
    receipt.raise_for_fee()

    assert not receipt.has_fee


def test_fees_come_from_the_chains_own_history(chain):
    fees = fees_from(chain, ceiling=gwei(60))

    assert fees.base_fee_per_gas == 24_310_000_000
    assert fees.max_priority_fee_per_gas == 1_100_000_000
    assert fees.max_fee_per_gas == 31_867_343_750
    assert not fees.capped
    assert fees.as_transaction_fields() == {
        "maxFeePerGas": 31_867_343_750,
        "maxPriorityFeePerGas": 1_100_000_000,
    }
    assert fees.explain().startswith("base fee 24.31 gwei, paying up to")


def test_a_binding_ceiling_caps_the_fee_and_says_so(chain):
    fees = fees_from(chain, ceiling=gwei(28))

    assert fees.capped
    assert fees.max_fee_per_gas == 28_000_000_000
    assert fees.max_priority_fee_per_gas == 1_100_000_000
    assert fees.headroom == 28_000_000_000 - 24_310_000_000 - 1_100_000_000
    assert "ceiling of 28 gwei" in fees.explain()


def test_a_ceiling_that_barely_clears_the_base_fee_trims_the_tip(chain):
    fees = fees_from(chain, ceiling=24_810_000_000)

    assert fees.max_fee_per_gas == 24_810_000_000
    assert fees.max_priority_fee_per_gas == 500_000_000
    assert fees.headroom == 0


def test_a_ceiling_under_the_base_fee_is_refused(chain):
    with pytest.raises(FeeCeilingTooLow):
        fees_from(chain, ceiling=gwei(20))


def test_the_priced_transaction_carries_the_encoded_call_data(chain, accounts, tokens):
    fees = fees_from(chain, ceiling=gwei(60))
    decimals = decode_decimals(chain.eth_call(tokens.usdt, encode_decimals()))
    data = encode_transfer(
        to=accounts.bob, amount=to_units(Decimal("1.5"), decimals=decimals)
    )
    transaction = {"to": tokens.usdt, "data": data, **fees.as_transaction_fields()}

    chain.send(
        to=transaction["to"], sender=accounts.alice, data=transaction["data"]
    )

    assert chain.sent[-1] == {
        "to": tokens.usdt.lower(),
        "from": accounts.alice,
        "data": data,
    }
    assert (
        transaction["maxFeePerGas"] - transaction["maxPriorityFeePerGas"]
        >= fees.base_fee_per_gas
    )
    assert balance_of(chain, tokens.usdt, accounts.bob) == 1_500_000
