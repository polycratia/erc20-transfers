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
    decode_transfer_result,
    decode_uint256,
    encode_allowance,
    encode_balance_of,
    encode_transfer,
    encode_transfer_from,
)

alice = "0x1111111111111111111111111111111111111111"
bob = "0x2222222222222222222222222222222222222222"

# Sending your own tokens.
data = encode_transfer(to=bob, amount=1_500_000)

# Spending someone else's, after checking the allowance you were given.
allowance = decode_uint256(eth_call(token, encode_allowance(owner=alice, spender=bob)))
if allowance >= 1_500_000:
    data = encode_transfer_from(sender=alice, to=bob, amount=1_500_000)

balance = decode_uint256(eth_call(token, encode_balance_of(account=alice)))
ok = decode_transfer_result(eth_call(token, data))
```

Only the minimal ERC-20 interface is encoded, so no ABI file is needed.
Addresses may be given in any case; EIP-55 checksums are not verified.

## License

MIT — see [LICENSE](LICENSE).

Maintained by [polycratia](https://polycratia.com).
