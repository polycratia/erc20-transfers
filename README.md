# erc20-transfers

ERC-20 transfers done right: allowances before `transferFrom`, gas strategy, and
transactions that are actually broadcast.

## Status

Pre-alpha. The public API is not stable yet.

## Installation

```bash
pip install erc20-transfers
```

From source:

```bash
git clone https://github.com/polycratia/erc20-transfers
cd erc20-transfers
pip install -e .
```

## Usage

Call data for the two transfer paths and the two reads that guard them:

```python
from erc20_transfers import (
    check_allowance,
    decode_transfer_result,
    decode_uint256,
    encode_allowance,
    encode_balance_of,
    encode_checked_transfer_from,
    encode_transfer,
)

alice = "0x1111111111111111111111111111111111111111"
bob = "0x2222222222222222222222222222222222222222"

# Sending your own tokens.
data = encode_transfer(to=bob, amount=1_500_000)

# Spending Alice's tokens as Bob: read the allowance, then encode against it.
allowance = decode_uint256(eth_call(token, encode_allowance(owner=alice, spender=bob)))
data = encode_checked_transfer_from(
    sender=alice, spender=bob, to=bob, amount=1_500_000, allowance=allowance
)

balance = decode_uint256(eth_call(token, encode_balance_of(account=alice)))
ok = decode_transfer_result(eth_call(token, data))
```

When the allowance is short, no call data is produced and `InsufficientAllowance`
carries the numbers:

```
0x2222...2222 holds an allowance of 400000 from 0x1111...1111, but transferFrom
of 1500000 needs 1100000 more; the call reverts until the owner approves at
least 1500000
```

To branch instead of raising, ask first:

```python
check = check_allowance(owner=alice, spender=bob, required=1_500_000, current=allowance)
if not check.sufficient:
    print(check.explain())  # also: check.shortfall, check.unlimited
```

### `.call()` is a simulation

`eth_call` — `.call()` in web3.py — runs a function against a local copy of
state and returns what it would have returned. It signs nothing and broadcasts
nothing, so a `True` from `transferFrom` under `.call()` means "this would
work", not "the tokens moved", and a revert from it means "this will not work"
rather than "a transaction failed". Moving tokens needs a signed transaction
and `eth_sendRawTransaction`.

Only the minimal ERC-20 interface is encoded, so no ABI file is needed.
Addresses may be given in any case; EIP-55 checksums are not verified.

## License

MIT — see [LICENSE](LICENSE).

Maintained by [polycratia](https://polycratia.com).
