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
  changes queue generation but preserves the original arrival sequence. The
  engine remembers each reference price and visits pegs only when it changes.
- **Order registry** maps IDs directly to active orders. Logical removal and
  generation tokens let stale heap entries be discarded lazily instead of
  requiring a linear search.
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

## Design decisions

- **Crossing limit orders execute.** The exercise permits either ignoring or
  filling a marketable limit order. This implementation fills it immediately,
  because that mirrors exchange behavior, uses already-available liquidity,
  and prevents a crossed book. Any unfilled quantity rests at its limit price.
- **The resting order sets the trade price.** Earlier liquidity made the price
  commitment, so an incoming market or crossing limit order executes at the
  maker's price rather than its own price.
- **Trade reports are aggregated by execution price.** Filling several FIFO
  orders at the same price produces one `Trade` with their combined quantity,
  matching the supplied example. Sweeping different prices produces one report
  per price in execution order. Unmatched market quantity is discarded.
- **Prices remain exact inside the book.** Strings and `Decimal` values can
  carry arbitrary decimal precision. Python floats are converted through
  `Decimal(str(value))`, with no two-decimal restriction or rounding. Bid keys
  use `Decimal.copy_negate()` to reverse
  price priority without the rounding that ordinary decimal negation can
  introduce. Quantities are positive whole numbers.
- **Amendment quantity means new remaining quantity.** A reduction keeps queue
  priority because it does not disadvantage other orders. An increase or an
  effective price change is treated as cancel-and-replace and receives a new
  arrival sequence. Zero quantity is rejected; cancellation is explicit.
- **Peg references exclude pegged orders.** Only regular limits establish the
  best bid or offer, avoiding circular or self-sustaining references. Only
  peg-to-bid buys and peg-to-offer sells are accepted. Automatic repricing
  preserves original priority, following the ordering shown in the exercise.
- **A peg needs a reference only at creation.** A new unreferenced peg is
  rejected. If an accepted peg later loses its last regular reference, it stays
  active at its last price and follows the reference again when one reappears,
  as clarified for the exercise.
- **Only reference changes require peg updates.** Partial fills and quantity
  reductions leave the reference price unchanged. A full fill can change it,
  so the engine checks the reference before selecting the next maker. Pegs
  keep their original sequence numbers; the queue heaps enforce FIFO without
  sorting the pegs during synchronization.
- **Heap removal is lazy.** Cancellation, full fills, amendments, and peg moves
  update the authoritative ID registry and generation tokens immediately.
  Obsolete heap entries are discarded when they reach a heap root, avoiding a
  linear search during each state change.
- **Public results are immutable.** Trades and snapshots are detached value
  objects, so callers cannot mutate live orders or corrupt queue invariants.

## Performance, roughly

| Operation | Cost to keep in mind |
| --- | --- |
| Active-order lookup or logical removal | Expected constant time |
| Insert or reprice a resting order | Logarithmic work in a few heaps |
| Find the best order or regular reference | Constant time at a valid root; extra work to remove stale entries |
| Synchronize a changed reference | Visit its pegs and update their heap entries |
| Match an incoming order | Work grows with the makers filled and any peg updates |
| Build a snapshot | Sort the active orders: `O(N log N)` |

Lazy invalidation moves heap cleanup to later lookups. Each stale entry is
removed at most once, but one lookup may clear several, and entries below a
valid root may stay in memory. Heap costs therefore depend on stored entries,
including stale ones, rather than just active orders. Cancelling through the
engine may also update pegs; only the registry removal itself is constant time.

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

All price input types may have more than two decimal places:

```python
engine.place_limit("buy", "10.251", 1)   # Exact string price
engine.place_limit("buy", 10.251, 1)     # Converted to Decimal("10.251")
engine.place_limit("buy", 0.1 + 0.2, 1)  # Decimal("0.30000000000000004")
```

The engine imposes no tick-size rule. Prefer strings or `Decimal` when exact
input matters: converting a float to text cannot recover precision lost in
earlier arithmetic. CLI prices arrive as strings.

## Assumptions and limitations

- Engine state and its ID sequence exist only for the lifetime of one process.
  Trades are returned to the caller but are not retained; there is no
  persistence, recovery, or trade-history service.
- The engine handles one asset on one thread. It has no networking,
  authentication, parallel order entry, or distributed sequencing.
- IDs are deterministic and process-local. Filled and cancelled orders leave
  the active registry and cannot subsequently be cancelled or amended.
- Prices must be positive and finite, with no application-level precision cap.
  Currency, tick size, maximum quantity, and regulatory constraints are outside
  scope.
- The lazy heaps can retain stale entries below their roots. This is appropriate
  for the bounded in-memory exercise, but a long-running production engine
  would need compaction or a different indexed data structure.
- Automatic peg repricing preserves original time priority because that is what
  the supplied example demonstrates; real venues may use different rules.
- The implementation prioritizes correctness and explainability over
  concurrency and production-grade latency benchmarking.

## Tests

The test suite uses only the Python standard library:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Tests cover the supplied examples, price-time priority, partial and multi-level
fills, amendments, cancellation, both peg sides, reference loss/reappearance,
CLI parsing, and batch execution. Regression cases exercise float conversion,
long decimal prices, altered decimal contexts, and unchanged peg references.
Seeded randomized lifecycles compare the engine with a simple reference model
and check trades, snapshots, quantities, active IDs, FIFO queues, peg references,
and the absence of a crossed book after operations.
