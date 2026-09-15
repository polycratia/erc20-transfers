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

## Moving tokens without losing them

A transfer that reverts is the cheap failure: nothing moved, the error says
why, and the next attempt can be better. The expensive failures are the quiet
ones. A payout a million times too large because the token counts in six
decimals and the code assumed eighteen. A `transferFrom` with no allowance
behind it. A `True` read off a simulation that signed nothing and broadcast
nothing.

Those three are the sections below. Everything after them — gas, and the tokens
that do not implement what they claim — is the same habit applied further out:
read what the chain says, and never stand in for it with a constant.

### The decimals trap

Every amount in this package is an integer of the token's smallest unit. How
many of those make one token is the token's own business: USDT and USDC use 6
decimals, DAI and most others 18, WBTC 8, a few none at all. Assuming 18
against a 6-decimal token scales every amount by 10\*\*12 — the difference
between paying out 1.5 USDT and paying out 1.5 million of them, in a
transaction that succeeds and cannot be taken back.

So nothing here has a default. `decimals` is read from the token and passed
explicitly:

```python
from decimal import Decimal

from erc20_transfers import (
    TokenAmount,
    decode_decimals,
    encode_balance_of,
    encode_decimals,
    encode_transfer,
    from_units,
    to_units,
)

alice = "0x1111111111111111111111111111111111111111"
bob = "0x2222222222222222222222222222222222222222"

decimals = decode_decimals(eth_call(token, encode_decimals()))

amount = to_units(Decimal("1.5"), decimals=decimals)  # 1500000 on USDT
data = encode_transfer(to=bob, amount=amount)

balance = TokenAmount.from_return_data(
    eth_call(token, encode_balance_of(account=alice)), decimals=decimals
)
print(balance.amount, balance.units)  # 1.500000 1500000
```

Conversion is exact: amounts are `Decimal`, floats are refused, and
`to_units(Decimal("1.0000005"), decimals=6)` raises rather than dropping the
digit the token cannot hold. `from_units` goes the other way for a value read
off the chain, and `TokenAmount` keeps an on-chain integer together with the
scale it was read at, so the number is harder to pass on without it.

### The allowance dance

`transfer` moves your own tokens and needs nothing but a balance. `transferFrom`
moves someone else's, and works only while that someone has approved the
spender for at least the amount being moved. Miss that and the call reverts
after the gas has been spent finding out.

Three steps, in order: read the allowance, raise it if it is short, encode the
transfer against the value you read.

```python
from erc20_transfers import (
    check_allowance,
    decode_uint256,
    encode_allowance,
    encode_checked_transfer_from,
    encode_transfer,
    plan_approval,
)

allowance = decode_uint256(eth_call(token, encode_allowance(owner=alice, spender=bob)))
```

The raise is where the dance comes in. USDT and a handful of others reject an
`approve` that moves a non-zero allowance straight to another non-zero value —
the check sits in their source and the transaction reverts. Going through zero
works on every token and costs one extra transaction, so it is the default:

```python
plan = plan_approval(spender=bob, current=allowance, required=1_500_000)
for step in plan.steps:
    send(token, step.data)  # in order, each waiting for its own receipt

print(plan.explain())
# two transactions: set the allowance of 400000 to zero, then approve 1500000; ...
```

`plan.needed` says whether anything has to be sent at all, `plan.resets_first`
whether the detour is in the plan, and `reset_first=False` skips it for a token
you know accepts the direct overwrite. `requires_zero_first_allowance(token)`
reads a short mainnet-only registry: a true answer means the reset is
mandatory, a false one only means the token is not on the list. Only raising an
allowance is planned — to lower one, plan for `required=0` and approve the new
value afterwards.

With the allowance in place, the transfer is encoded against it rather than
against hope:

```python
data = encode_checked_transfer_from(
    sender=alice, spender=bob, to=bob, amount=1_500_000, allowance=allowance
)
```

When the allowance is short no call data is produced, and `InsufficientAllowance`
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

Sending your own tokens skips the whole figure:

```python
data = encode_transfer(to=bob, amount=1_500_000)
```

### `.call()` is not a send

`eth_call` — `.call()` in web3.py — runs a function against a local copy of
state and hands back what it would have returned. Nothing is signed, nothing
reaches a mempool, no receipt is produced. A `True` from `transferFrom` under
`.call()` means "this would work"; it never means "the tokens moved". A revert
from it means "this will not work", not "a transaction failed".

Use it for the reads that guard a transfer — `allowance`, `balanceOf`,
`decimals` — and for a dry run of the transfer itself. Moving tokens needs a
signed transaction and `eth_sendRawTransaction`:

```python
from erc20_transfers import decode_transfer_result, decode_uint256, encode_balance_of

balance = decode_uint256(eth_call(token, encode_balance_of(account=alice)))
would_work = decode_transfer_result(eth_call(token, data))  # a rehearsal, not a send
# ... sign `data` into a transaction and broadcast it; the tokens move there ...
```

The receipt that comes back is necessary and still not sufficient: a token may
return `false` without reverting, and the receipt carries status 1 all the same.
What the transfer itself returned is the last word, and the tokens that make
that awkward are below.

## Gas is priced by the chain, not by a constant

Since London a transaction names `maxFeePerGas` and `maxPriorityFeePerGas`
rather than one `gasPrice`. The base fee in between is set by the protocol,
burned, moves by up to 1/8 per block, and is refunded above what the block
charged — so both numbers are read from recent blocks and bounded by a ceiling
you choose:

```python
from erc20_transfers import estimate_fees, gwei, priority_fee_from_history

history = eth_fee_history(10, "latest", [50])  # ten blocks, median tip

fees = estimate_fees(
    base_fee_per_gas=history["baseFeePerGas"][-1],  # what the next block charges
    priority_fee_per_gas=priority_fee_from_history(
        [block[0] for block in history["reward"]]
    ),
    max_fee_ceiling=gwei(60),
)

tx = {"to": token, "data": data, **fees.as_transaction_fields()}
print(fees.explain())
# base fee 24.31 gwei, paying up to 31.87 gwei with a 1.1 gwei tip
```

`max_fee_ceiling` is required and never exceeded. When it binds, `fees.capped`
is true and `explain()` says what was given up; when it sits below the base fee
the next block will charge, `FeeCeilingTooLow` is raised instead of returning
fees no block would accept. `headroom_blocks` (2 by default) decides how much
base-fee growth the transaction can outlast while it waits; `base_fee_headroom`
and `next_base_fee` expose that arithmetic on its own.

## Tokens that do not follow the standard

ERC-20 is an interface, not an enforcement. Two deviations remain beyond the
allowance reset already covered, and both are common enough among the most
traded tokens that ignoring them is a bug rather than a simplification.

**A transfer that returns nothing.** USDT and its contemporaries were written
before the `bool` return was settled; they revert on failure and return no data
on success. Reading that empty return as a failure stalls a payment that
already happened, so silence is a success — and a word that is neither `0` nor
`1` is refused rather than read as truthy, because a token returning something
else is not described by the interface it claims to implement:

```python
from erc20_transfers import read_transfer_return

result = read_transfer_return(return_data)  # from the transfer's own call
result.raise_for_status()                   # TransferRejected on an explicit false
print(result.explain())
# the token returned nothing; it predates the finalised standard, so the call
# not reverting is the only success signal there is
```

A `false` return is the quiet case: the tokens did not move, the gas was spent,
and the receipt still carries status 1. `raise_for_status()` turns it into
`TransferRejected`; unreadable data raises `NonBooleanReturn`.

**A transfer that delivers less than it was given.** Fee-taking tokens deduct
on the way through, and the deduction appears in neither the call data nor the
receipt. The only honest source is the recipient's balance, read before the
transfer and again after it:

```python
from erc20_transfers import decode_uint256, encode_balance_of, measure_received

before = decode_uint256(eth_call(token, encode_balance_of(account=bob)))
# ... broadcast the transfer, wait for its receipt ...
after = decode_uint256(eth_call(token, encode_balance_of(account=bob)))

receipt = measure_received(sent=1_000_000, balance_before=before, balance_after=after)
receipt.raise_for_fee()  # FeeOnTransfer when short
print(receipt.explain())
# 950000 of 1000000 units arrived; the token kept 50000 (500.00 bps), so
# anything downstream that was promised 1000000 is short
```

Grossing an amount up so the net comes out exact is not offered: inverting a
fee schedule nobody published is guesswork, and the schedule can change between
blocks.

## Scope

Only the minimal ERC-20 surface is encoded — `transfer`, `transferFrom`,
`approve`, `allowance`, `balanceOf`, `decimals` — so no ABI file and no parser
for one is needed. Addresses may be given in any case; EIP-55 checksums are not
verified, since verifying them needs keccak-256 and the package has no
dependencies.

## License

MIT — see [LICENSE](LICENSE).

Maintained by [polycratia](https://polycratia.com).
