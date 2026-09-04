# Order Matching System

A dependency-free, single-asset matching engine written in Python 3.12. The
current implementation supports resting limit and pegged orders, market orders,
crossing limit orders, partial fills, cancellation, amendment, and immutable
book snapshots.

## Architecture

```text
Incoming order
      |
      v
MatchingEngine  ----->  OperationResult / Trade
      |
      v
LimitOrderBook  ----->  BookSnapshot
```

- **`MatchingEngine`** owns order validation, deterministic ID generation,
  crossing rules, and execution. Incoming orders act as takers and execute at
  the resting maker's price. Fills at the same price are aggregated into one
  trade report.
- **`LimitOrderBook`** stores regular and pegged resting liquidity and applies
  price-time priority. Separate bid and offer heaps select the best price; each
  price level has a second heap ordered by arrival sequence to enforce FIFO.
  A separate lazy index tracks only regular limits for peg references.
- **Peg synchronization** belongs to `MatchingEngine`. A buy peg follows the
  best regular bid and a sell peg follows the best regular offer. Repricing
  changes queue generation but preserves the original arrival sequence.
- **Order registry** maps IDs directly to active orders. Cancellation-ready
  logical removal and generation tokens let stale heap entries be discarded
  lazily instead of requiring a linear search.
- **Domain models** provide validated enums and immutable public values such as
  `Trade`, `OperationResult`, `BookEntry`, and `BookSnapshot`. Prices use
  `Decimal` rather than binary floating point.
- **CommandProcessor** translates text commands into public `MatchingEngine`
  calls. The stream runner adds prompts only for a terminal and reads batch
  commands without prompts until EOF.

## Matching rules

- Highest bid and lowest offer have price priority.
- Orders at the same price execute in arrival order.
- Trades use the resting order's price.
- A crossing limit order executes immediately and rests any remainder.
- An unfilled market-order remainder is discarded.
- Only resting orders remain in the active book.
- Quantity reductions retain FIFO priority; quantity increases and price
  changes lose it.
- A new peg requires an existing regular same-side reference. Pegs never define
  another peg's reference.
- If the last regular reference disappears, an existing peg remains executable
  at its last price. It follows the reference again when regular liquidity
  reappears.
- Peg quantity reductions preserve priority and increases lose it. A peg's
  derived price cannot be amended directly.

## Data-structure costs

| Operation | Expected cost |
| --- | --- |
| Active-order lookup or logical removal | `O(1)` |
| Rest a regular order | `O(log P + log Q + log N)` |
| Rest a pegged order | `O(log P + log Q)` |
| Read the best order | `O(1)` when clean; stale cleanup is amortized |
| Read the best regular reference | `O(1)` when clean; stale cleanup is amortized |
| Reprice a peg | `O(log P + log Q)` |
| Build a sorted snapshot | `O(N log N)` |

`P` is the number of price levels, `Q` the number of orders at one level, and
`N` the total number of active orders.

## Command interface

Start an interactive session:

```bash
python3 -m matching_engine
```

Or process a file or redirected input in batch mode:

```bash
python3 -m matching_engine < commands.txt
```

Supported commands:

```text
limit <buy|sell> <price> <qty>
market <buy|sell> <qty>
peg <bid|offer> <buy|sell> <qty>
cancel order <order_id>
amend order <order_id> [price <price>] [qty <qty>]
print book
help
quit
```

Example:

```text
>>> limit sell 20 100
Order created: sell 100 @ 20 order_000001
>>> market buy 25
Trade, price: 20, qty: 25
>>> print book
Buy orders | Sell orders
-----------+-----------------------
(empty)    | 75 @ 20 [order_000001]
```

## Current API

```python
from matching_engine import MatchingEngine

engine = MatchingEngine()
engine.place_limit("sell", "20", 100)
result = engine.place_market("buy", 50)

order = engine.place_limit("buy", "10", 100)
peg = engine.place_pegged("buy", "bid", 25)
engine.amend(order.order_id, quantity=75)
engine.cancel(peg.order_id)

print(result.trades)
print(engine.snapshot())
```

## Tests

The test suite uses only the Python standard library:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The project is intentionally in memory, single-threaded, and limited to one
asset.
