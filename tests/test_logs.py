"""Hand-built logs, read the way a receipt is read.

The shapes here are what a node returns: an address per log, topics and data as
0x strings, other events mixed in among the transfers. A log is the one part of
a receipt that can be written out by hand and still be byte for byte what the
chain emitted, so nothing is scripted - the values below are the vectors.
"""

import pytest

from erc20_transfers import (
    TRANSFER_TOPIC,
    NotATransferLog,
    NothingMoved,
    TransactionReverted,
    TransferEvent,
    confirm_transfers,
    decode_transfer_log,
    find_transfers,
)

ALICE = "0x1111111111111111111111111111111111111111"
BOB = "0x2222222222222222222222222222222222222222"
CAROL = "0x3333333333333333333333333333333333333333"
FEE_COLLECTOR = "0x4444444444444444444444444444444444444444"
NFT = "0x5555555555555555555555555555555555555555"

DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
DAI_LOWER = DAI.lower()
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDT_LOWER = USDT.lower()

TRANSFER_TOPIC_HEX = "0x" + TRANSFER_TOPIC.hex()
# Approval(address,address,uint256): some other event in the same receipt.
APPROVAL_TOPIC_HEX = (
    "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
)


def address_topic(address: str) -> str:
    return "0x" + address[2:].lower().rjust(64, "0")


def value_word(value: int) -> str:
    return "0x" + f"{value:064x}"


def transfer_log(*, token=DAI, sender=ALICE, to=BOB, value=1_500_000) -> dict:
    return {
        "address": token,
        "topics": [TRANSFER_TOPIC_HEX, address_topic(sender), address_topic(to)],
        "data": value_word(value),
    }


def approval_log(*, token=DAI, owner=ALICE, spender=BOB, value=1_500_000) -> dict:
    return {
        "address": token,
        "topics": [APPROVAL_TOPIC_HEX, address_topic(owner), address_topic(spender)],
        "data": value_word(value),
    }


def nft_transfer_log(*, sender=ALICE, to=BOB, token_id=7) -> dict:
    return {
        "address": NFT,
        "topics": [
            TRANSFER_TOPIC_HEX,
            address_topic(sender),
            address_topic(to),
            value_word(token_id),
        ],
        "data": "0x",
    }


def test_transfer_topic_is_the_one_the_standard_fixes():
    assert TRANSFER_TOPIC.hex() == (
        "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    )


def test_a_log_decodes_into_who_moved_what():
    event = decode_transfer_log(transfer_log())

    assert event == TransferEvent(
        token=DAI_LOWER, sender=ALICE, to=BOB, value=1_500_000
    )
    assert "1500000" in event.explain()


def test_bytes_and_hex_logs_decode_alike():
    raw = {
        "address": DAI,
        "topics": [
            TRANSFER_TOPIC,
            bytes.fromhex(address_topic(ALICE)[2:]),
            bytes.fromhex(address_topic(BOB)[2:]),
        ],
        "data": (42).to_bytes(32, "big"),
    }

    assert decode_transfer_log(raw) == decode_transfer_log(transfer_log(value=42))


def test_another_event_is_not_a_transfer():
    with pytest.raises(NotATransferLog):
        decode_transfer_log(approval_log())


def test_an_nft_transfer_is_refused_rather_than_read_as_an_amount():
    with pytest.raises(NotATransferLog):
        decode_transfer_log(nft_transfer_log(token_id=7))


def test_a_malformed_transfer_log_is_refused():
    with pytest.raises(NotATransferLog):
        decode_transfer_log(transfer_log() | {"data": "0x" + "00" * 31})
    with pytest.raises(NotATransferLog):
        decode_transfer_log(
            {
                "address": DAI,
                "topics": [TRANSFER_TOPIC_HEX, "0x" + "11" * 32, address_topic(BOB)],
                "data": value_word(1),
            }
        )
    with pytest.raises(NotATransferLog):
        decode_transfer_log({"address": DAI, "topics": [], "data": "0x"})
    with pytest.raises(NotATransferLog):
        decode_transfer_log({"address": DAI, "data": value_word(1)})


def test_find_transfers_keeps_the_transfers_and_their_order():
    logs = [
        approval_log(),
        transfer_log(token=USDT, to=CAROL, value=1),
        transfer_log(token=DAI, to=BOB, value=2),
        transfer_log(token=DAI, sender=CAROL, to=CAROL, value=3),
        nft_transfer_log(),
    ]

    assert [event.value for event in find_transfers(logs)] == [1, 2, 3]
    assert [event.value for event in find_transfers(logs, token=DAI)] == [2, 3]
    assert [event.value for event in find_transfers(logs, to=CAROL)] == [1, 3]
    assert [
        event.value for event in find_transfers(logs, token=USDT, to=CAROL)
    ] == [1]
    assert find_transfers(logs, token=DAI_LOWER, to=FEE_COLLECTOR) == ()
    assert find_transfers(logs)[0].token == USDT_LOWER


def test_a_log_without_topics_is_passed_over():
    assert find_transfers([{"address": DAI, "data": value_word(1)}]) == ()


def test_a_reverted_transaction_moved_nothing():
    with pytest.raises(TransactionReverted) as raised:
        confirm_transfers({"status": 0, "logs": []}, token=DAI, to=BOB)

    assert raised.value.token == DAI_LOWER


def test_status_one_without_a_log_is_how_a_false_return_looks():
    receipt = {"status": 1, "logs": [approval_log()]}

    with pytest.raises(NothingMoved) as raised:
        confirm_transfers(receipt, token=DAI, to=BOB)

    assert raised.value.token == DAI_LOWER
    assert raised.value.to == BOB
    assert "status 1" in str(raised.value)


def test_a_transfer_to_someone_else_does_not_confirm_this_one():
    receipt = {"status": 1, "logs": [transfer_log(to=CAROL)]}

    with pytest.raises(NothingMoved):
        confirm_transfers(receipt, token=DAI, to=BOB)


def test_a_confirmed_transfer_carries_its_numbers():
    receipt = {
        "status": "0x1",
        "logs": [approval_log(), transfer_log(value=1_500_000)],
    }

    events = confirm_transfers(receipt, token=DAI, to=BOB)

    assert [event.value for event in events] == [1_500_000]
    assert events[0].sender == ALICE
    assert events[0].token == DAI_LOWER


def test_a_fee_token_logs_what_arrived_not_what_was_sent():
    receipt = {
        "status": 1,
        "logs": [
            transfer_log(token=USDT, to=BOB, value=975_000),
            transfer_log(token=USDT, to=FEE_COLLECTOR, value=25_000),
        ],
    }

    events = confirm_transfers(receipt, token=USDT, to=BOB)

    assert [event.value for event in events] == [975_000]


def test_several_credits_in_one_transaction_are_all_returned():
    receipt = {
        "status": 1,
        "logs": [transfer_log(value=1), transfer_log(sender=CAROL, value=2)],
    }

    assert [
        event.value for event in confirm_transfers(receipt, token=DAI, to=BOB)
    ] == [1, 2]


def test_a_receipt_without_a_status_or_logs_proves_nothing():
    with pytest.raises(ValueError):
        confirm_transfers({"logs": []}, token=DAI, to=BOB)
    with pytest.raises(ValueError):
        confirm_transfers({"status": 1}, token=DAI, to=BOB)
    with pytest.raises(ValueError):
        confirm_transfers({"status": 2, "logs": []}, token=DAI, to=BOB)
