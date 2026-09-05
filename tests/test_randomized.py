"""Compare mixed lifecycles with a simple oracle and check storage invariants."""

from collections import Counter
from random import Random
import unittest

from matching_engine import (
    MatchingEngine,
    MissingReferenceError,
    OrderType,
    PegReference,
    Side,
    UnknownOrderError,
    ValidationError,
)
from reference_engine import ReferenceEngine, ReferenceMissingReferenceError


class RandomizedMatchingTests(unittest.TestCase):
    SEEDS = range(5)
    OPERATIONS_PER_SEED = 300

    def _run_engine(self, engine, command):
        operation, *args = command
        if operation == "limit":
            result = engine.place_limit(*args)
        elif operation == "market":
            result = engine.place_market(*args)
        elif operation == "peg":
            result = engine.place_pegged(*args)
        elif operation == "cancel":
            engine.cancel(*args)
            return None, ()
        else:
            order_id, fields = args
            result = engine.amend(order_id, **fields)
        return result.order_id, tuple((trade.price, trade.qty) for trade in result.trades)

    def _run_reference(self, reference, command):
        operation, *args = command
        if operation == "limit":
            return reference.limit(*args)
        if operation == "market":
            return None, reference.market(*args)
        if operation == "peg":
            return reference.peg_order(*args), ()
        if operation == "cancel":
            reference.cancel(*args)
            return None, ()
        order_id, fields = args
        return reference.amend(order_id, **fields)

    def _step(self, engine, reference, command):
        before = engine.snapshot()
        before_reference = reference.snapshot()
        old_quantities = {entry[0]: entry[3] for entry in before_reference}
        total_before = sum(old_quantities.values())
        old_next_id = engine._next_order_id

        # Run the oracle first. A rejection must agree in type and leave both
        # engines unchanged; do not swallow an exception from just one engine.
        expected_error = None
        try:
            expected = self._run_reference(reference, command)
        except ReferenceMissingReferenceError:
            expected_error = MissingReferenceError
        except KeyError:
            expected_error = UnknownOrderError
        except ValueError:
            expected_error = ValidationError

        if expected_error is not None:
            with self.assertRaises(expected_error):
                self._run_engine(engine, command)
            self.assertEqual(engine.snapshot(), before)
            self.assertEqual(reference.snapshot(), before_reference)
            self.assertEqual(engine._next_order_id, old_next_id)
            total_after = total_before
            result = None, ()
        else:
            result = self._run_engine(engine, command)
            self.assertEqual(result, expected)
            filled = sum(quantity for _, quantity in result[1])
            operation = command[0]
            if operation == "limit":
                # Each fill removes one maker share and uses one submitted share.
                total_after = total_before + command[3] - 2 * filled
            elif operation == "market":
                total_after = total_before - filled
            elif operation == "peg":
                total_after = total_before + command[3]
            elif operation == "cancel":
                total_after = total_before - old_quantities[command[1]]
            else:
                old_quantity = old_quantities[command[1]]
                new_quantity = command[2].get("quantity", old_quantity)
                total_after = total_before + new_quantity - old_quantity - 2 * filled

        self._assert_invariants(engine, reference)
        entries = (*engine.snapshot().bids, *engine.snapshot().offers)
        self.assertEqual(sum(entry.quantity for entry in entries), total_after)
        self.assertEqual(engine._next_order_id, reference.next_id)
        return result

    def _assert_invariants(self, engine, reference):
        snapshot = engine.snapshot()
        entries = (*snapshot.bids, *snapshot.offers)
        actual = tuple(
            (entry.order_id, entry.side.value, entry.price, entry.quantity,
             entry.order_type is OrderType.PEGGED,
             entry.peg_reference.value if entry.peg_reference else None)
            for entry in entries
        )
        self.assertEqual(actual, reference.snapshot())
        ids = [entry.order_id for entry in entries]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(engine._book._orders), set(ids))
        self.assertTrue(all(entry.quantity > 0 for entry in entries))
        if snapshot.bids and snapshot.offers:
            self.assertLess(snapshot.bids[0].price, snapshot.offers[0].price)

        for side in Side:
            expected_orders = reference.ordered(side.value)
            expected_best = expected_orders[0] if expected_orders else None
            best = engine._book.best(side)
            self.assertEqual(
                best.order_id if best else None,
                expected_best.order_id if expected_best else None,
            )
            expected_regular = reference.best_regular(side.value)
            regular = engine._book.best_regular(side)
            self.assertEqual(
                regular.order_id if regular else None,
                expected_regular.order_id if expected_regular else None,
            )
            peg_reference = PegReference.BID if side is Side.BUY else PegReference.OFFER
            self.assertEqual(
                engine._last_reference_prices[peg_reference],
                expected_regular.price if expected_regular else None,
            )
            self.assertEqual(
                engine._pegged_ids[peg_reference],
                {order.order_id for order in expected_orders if order.peg},
            )

        # Lazy heaps may contain obsolete versions, but each active order must
        # have exactly one current FIFO entry at its current price.
        for order in engine._book._orders.values():
            self.assertTrue(order.active)
            level = engine._book._levels[(order.side, order.price)]
            self.assertEqual(
                level.entries.count((order.sequence, order.generation, order.order_id)),
                1,
            )
            if order.order_type is OrderType.LIMIT:
                price_key = (order.price.copy_negate()
                             if order.side is Side.BUY else order.price)
                self.assertEqual(
                    engine._book._regular_heaps[order.side].count(
                        (price_key, order.sequence, order.generation,
                         order.order_id, order.price)
                    ),
                    1,
                )
        for level in engine._book._levels.values():
            for child in range(1, len(level.entries)):
                self.assertLessEqual(
                    level.entries[(child - 1) // 2], level.entries[child]
                )

    def _next_command(self, rng, reference, retired_ids):
        operation = rng.choices(
            ("limit", "market", "peg", "cancel", "amend", "retired", "invalid"),
            weights=(30, 20, 12, 12, 18, 4, 4),
        )[0]
        side = rng.choice(("buy", "sell"))
        price = rng.choice(("8", "8.75", "9", "10", "10.25", "11", "12", "13", "14"))
        quantity = rng.randint(1, 12)
        if operation == "limit":
            return "limit", side, price, quantity
        if operation == "market":
            return "market", side, quantity
        if operation == "peg":
            return "peg", side, "bid" if side == "buy" else "offer", quantity
        if operation == "retired" and retired_ids:
            order_id = rng.choice(sorted(retired_ids))
            return (("cancel", order_id) if rng.randrange(2)
                    else ("amend", order_id, {"quantity": quantity}))
        if operation == "invalid":
            return "market", side, 0
        if not reference.orders:
            return "limit", side, price, quantity

        order = rng.choice(reference.orders)
        if operation == "cancel":
            return "cancel", order.order_id
        if order.peg:
            fields = {"quantity": quantity}
        else:
            fields = rng.choice((
                {"price": price}, {"quantity": quantity},
                {"price": price, "quantity": quantity},
            ))
        return "amend", order.order_id, fields

    def test_seeded_mixed_operations_match_linear_oracle(self):
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                rng = Random(seed)
                engine = MatchingEngine()
                reference = ReferenceEngine()
                known_ids = set()
                coverage = Counter()

                # These transitions are guaranteed for both sides, independent
                # of the random stream: peg survives consumption of its last
                # reference, rejects a new peg, follows a returning reference,
                # and survives a second gap with same-price reappearance.
                prefix = (
                    ("limit", "buy", "10", 3),
                    ("limit", "buy", "9", 4),
                    ("peg", "buy", "bid", 3),
                    ("limit", "sell", "11", 3),
                    ("limit", "sell", "12", 4),
                    ("peg", "sell", "offer", 3),
                    ("market", "sell", 8),
                    ("peg", "buy", "bid", 1),
                    ("limit", "buy", "8", 2),
                    ("cancel", "order_000007"),
                    ("limit", "buy", "8", 2),
                    ("market", "buy", 8),
                    ("peg", "sell", "offer", 1),
                    ("limit", "sell", "13", 2),
                    ("cancel", "order_000009"),
                    ("limit", "sell", "13", 2),
                    ("cancel", "order_000001"),
                    ("amend", "order_000001", {"quantity": 2}),
                    ("amend", "order_000008", {"quantity": 0}),
                    ("amend", "order_000003", {"price": "8"}),
                    ("limit", "buy", "13", 1),
                    ("limit", "sell", "8", 1),
                )
                for step in range(len(prefix) + self.OPERATIONS_PER_SEED):
                    active_ids = {order.order_id for order in reference.orders}
                    command = (prefix[step] if step < len(prefix) else
                               self._next_command(rng, reference, known_ids - active_ids))
                    previous_references = {
                        side: reference.best_regular(side) is not None
                        for side in ("buy", "sell")
                    }
                    with self.subTest(seed=seed, step=step, command=command):
                        order_id, trades = self._step(engine, reference, command)
                        if order_id is not None:
                            known_ids.add(order_id)
                        coverage[command[0]] += 1
                        if trades:
                            coverage["trading_operations"] += 1
                        for side, had_reference in previous_references.items():
                            has_reference = reference.best_regular(side) is not None
                            if had_reference and not has_reference:
                                coverage["lost_" + side] += 1
                            if not had_reference and has_reference:
                                coverage["returned_" + side] += 1

                for event in ("limit", "market", "peg", "cancel", "amend",
                              "trading_operations", "lost_buy", "lost_sell",
                              "returned_buy", "returned_sell"):
                    self.assertGreater(coverage[event], 0, event)


if __name__ == "__main__":
    unittest.main()
