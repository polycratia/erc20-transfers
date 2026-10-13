"""The Transfer log: the chain's own record that tokens actually moved.

A receipt with status 1 says the transaction executed without reverting. It
does not say the balances changed - a token may return `false` instead of
reverting, and that receipt carries status 1 all the same. What separates the
two is the log: a transfer that moved tokens emits
`Transfer(address indexed from, address indexed to, uint256 value)`, so the
log is the proof and its absence is the failure.

A topic is the keccak-256 hash of the event signature, the way a selector is
the first four bytes of the hash of a function signature. This one is fixed by
the standard, so it is spelled out next to the signature it comes from and the
package stays free of a keccak dependency. Amounts are read from the log and
never from what was asked for: a fee-taking token logs what arrived.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from erc20_transfers.abi import WORD_SIZE, normalize_address

__all__ = [
    "NotATransferLog",
    "NothingMoved",
    "TRANSFER_TOPIC",
    "TransactionReverted",
    "TransferEvent",
    "confirm_transfers",
    "decode_transfer_log",
    "find_transfers",
]

TRANSFER_TOPIC: Final = bytes.fromhex(
    "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)  # Transfer(address,address,uint256)

# The signature hash plus the two indexed addresses. ERC-721 shares the
# signature and indexes the token id as well, which is why the count matters.
_TRANSFER_TOPICS: Final = 3
_ADDRESS_OFFSET: Final = WORD_SIZE - 20
_LOG_KEYS: Final = ("address", "topics", "data")


class NotATransferLog(ValueError):
    """Raised for a log that is not an ERC-20 `Transfer`."""


class TransactionReverted(RuntimeError):
    """Raised for a receipt with status 0: nothing moved, the gas was spent."""

    def __init__(self, *, token: str, to: str) -> None:
        super().__init__(
            f"the transaction reverted with status 0; no tokens reached {to} from "
            f"{token}, no state changed, and the gas was spent finding out"
        )
        self.token = token
        self.to = to


class NothingMoved(RuntimeError):
    """Raised for a receipt that succeeded without emitting the transfer."""

    def __init__(self, *, token: str, to: str) -> None:
        super().__init__(
            f"the transaction carries status 1 but no Transfer log in it credited "
            f"{to} in {token}; a token that returns false without reverting looks "
            f"exactly like this, so the tokens did not move"
        )
        self.token = token
        self.to = to


@dataclass(frozen=True, slots=True)
class TransferEvent:
    """One `Transfer` log, decoded: which token moved how much, and between whom."""

    token: str
    sender: str
    to: str
    value: int

    def explain(self) -> str:
        """Say in one sentence what the chain recorded."""
        return (
            f"{self.value} units of {self.token} moved from {self.sender} to "
            f"{self.to}; the log is in the receipt, so this is what the chain "
            f"recorded rather than what the call data asked for"
        )


def _as_bytes(value: object, name: str) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if not isinstance(value, str):
        raise NotATransferLog(
            f"{name} must be bytes or a hex string, got {type(value).__name__}"
        )

    digits = value[2:] if value[:2].lower() == "0x" else value
    try:
        return bytes.fromhex(digits)
    except ValueError as exc:
        raise NotATransferLog(f"{name} is not valid hex: {value!r}") from exc


def _word(value: object, name: str) -> bytes:
    raw = _as_bytes(value, name)
    if len(raw) != WORD_SIZE:
        raise NotATransferLog(
            f"{name} must be {WORD_SIZE} bytes, got {len(raw)}: 0x{raw.hex()}"
        )

    return raw


def _address_from_topic(value: object, name: str) -> str:
    raw = _word(value, name)
    if any(raw[:_ADDRESS_OFFSET]):
        raise NotATransferLog(
            f"{name} is not a left-padded address: 0x{raw.hex()}"
        )

    return "0x" + raw[_ADDRESS_OFFSET:].hex()


def _topics(log: Mapping[str, Any]) -> Sequence[Any] | None:
    topics = log.get("topics")
    if not isinstance(topics, Sequence) or isinstance(topics, (str, bytes, bytearray)):
        return None

    return topics


def _is_transfer_log(log: Mapping[str, Any]) -> bool:
    topics = _topics(log)
    if topics is None or len(topics) != _TRANSFER_TOPICS:
        return False
    try:
        return _word(topics[0], "topic 0") == TRANSFER_TOPIC
    except NotATransferLog:
        return False


def decode_transfer_log(log: Mapping[str, Any]) -> TransferEvent:
    """Read one receipt log into the transfer it records.

    `log` is a log as a node returns it: an `address`, a list of 32-byte
    `topics` and the non-indexed `data`, each either bytes or a hex string. A
    log whose first topic is not `TRANSFER_TOPIC` is refused, and so is one
    with a fourth topic: ERC-721 shares the signature but indexes the token id
    where ERC-20 leaves the value in the data, and reading one as the other
    turns an id into an amount.
    """
    if not isinstance(log, Mapping):
        raise TypeError(f"log must be a mapping, got {type(log).__name__}")
    for key in _LOG_KEYS:
        if key not in log:
            raise NotATransferLog(f"log is missing {key!r}")

    topics = _topics(log)
    if topics is None:
        raise NotATransferLog("topics must be a sequence of 32-byte words")
    if not topics or _word(topics[0], "topic 0") != TRANSFER_TOPIC:
        raise NotATransferLog(
            "the first topic is not the Transfer signature hash; this log records "
            "some other event"
        )
    if len(topics) != _TRANSFER_TOPICS:
        raise NotATransferLog(
            f"a Transfer log with {len(topics)} topics is not ERC-20: ERC-721 "
            f"indexes the token id as well, so its value word is an id and not "
            f"an amount"
        )

    value = _word(log["data"], "data")

    return TransferEvent(
        token=normalize_address(log["address"]),
        sender=_address_from_topic(topics[1], "topic 1"),
        to=_address_from_topic(topics[2], "topic 2"),
        value=int.from_bytes(value, "big"),
    )


def find_transfers(
    logs: Sequence[Mapping[str, Any]],
    *,
    token: str | None = None,
    to: str | None = None,
) -> tuple[TransferEvent, ...]:
    """The ERC-20 transfers among a receipt's logs, in the order they were emitted.

    Logs that record another event, or a `Transfer` of something that is not an
    ERC-20, are passed over rather than refused: one transaction may touch
    several contracts and most of what it emits is none of this package's
    business. `token` and `to` narrow the result to one contract and one
    recipient, in any case.
    """
    wanted_token = None if token is None else normalize_address(token)
    wanted_to = None if to is None else normalize_address(to)

    found = []
    for index, log in enumerate(logs):
        if not isinstance(log, Mapping):
            raise TypeError(f"log {index} must be a mapping, got {type(log).__name__}")
        if not _is_transfer_log(log):
            continue
        event = decode_transfer_log(log)
        if wanted_token is not None and event.token != wanted_token:
            continue
        if wanted_to is not None and event.to != wanted_to:
            continue
        found.append(event)

    return tuple(found)


def _status(receipt: Mapping[str, Any]) -> int:
    if not isinstance(receipt, Mapping):
        raise TypeError(f"receipt must be a mapping, got {type(receipt).__name__}")
    if "status" not in receipt:
        raise ValueError(
            "the receipt has no status; a transaction that has not been mined has "
            "moved nothing yet"
        )

    raw = receipt["status"]
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise TypeError(
            f"status must be an int or a hex string, got {type(raw).__name__}"
        )
    if isinstance(raw, str):
        try:
            raw = int(raw, 16)
        except ValueError:
            raise ValueError(f"status is not a hex integer: {raw!r}") from None
    if raw not in (0, 1):
        raise ValueError(f"status must be 0 or 1, got {raw}")

    return raw


def confirm_transfers(
    receipt: Mapping[str, Any], *, token: str, to: str
) -> tuple[TransferEvent, ...]:
    """Prove from a receipt that `to` was credited in `token`, or say why not.

    Status 0 raises `TransactionReverted`: the call failed and nothing moved.
    Status 1 with no matching log raises `NothingMoved`, which is what a token
    returning `false` without reverting leaves behind - a successful receipt
    over balances that did not change. Everything else comes back as the logs
    that credited `to`, usually one, more when a transaction pays a recipient
    in several steps.
    """
    status = _status(receipt)
    address = normalize_address(token)
    recipient = normalize_address(to)
    if status == 0:
        raise TransactionReverted(token=address, to=recipient)
    if "logs" not in receipt:
        raise ValueError(
            "the receipt has no logs; without them a status of 1 says only that "
            "the transaction executed"
        )

    events = find_transfers(receipt["logs"], token=address, to=recipient)
    if not events:
        raise NothingMoved(token=address, to=recipient)

    return events
