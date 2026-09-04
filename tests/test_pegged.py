import unittest
from decimal import Decimal
from random import Random

from matching_engine import (
    LimitOrderBook,
    MatchingEngine,
    MissingReferenceError,
    OrderType,
    PegReference,
    RestingOrder,
    Side,
    Trade,
    UnknownOrderError,
    ValidationError,
)


class PeggedExerciseExampleTests(unittest.TestCase):
    def test_supplied_bid_peg_example_preserves_original_priority(self):
        engine = MatchingEngine()
        engine.place_limit("buy", "10", 200)
        engine.place_limit("buy", "9.99", 100)
        engine.place_limit("sell", "10.5", 100)

        peg = engine.place_pegged("buy", "bid", 150)
        new_best = engine.place_limit("buy", "10.1", 300)

        bids = engine.snapshot().bids
        self.assertEqual(
            [(entry.order_id, entry.quantity, entry.price) for entry in bids],
            [
                (peg.order_id, 150, Decimal("10.1")),
                (new_best.order_id, 300, Decimal("10.1")),
                ("order_000001", 200, Decimal("10")),
                ("order_000002", 100, Decimal("9.99")),
            ],
        )


class PeggedCreationTests(unittest.TestCase):
    def test_creation_requires_regular_reference_and_does_not_consume_id(self):
        engine = MatchingEngine()

        with self.assertRaises(MissingReferenceError):
            engine.place_pegged("buy", "bid", 10)

        self.assertEqual(
            engine.place_limit("buy", "10", 10).order_id,
            "order_000001",
        )

    def test_only_same_side_reference_combinations_are_valid(self):
        engine = MatchingEngine()
        engine.place_limit("buy", "10", 10)
        engine.place_limit("sell", "11", 10)

        invalid = (("buy", "offer"), ("sell", "bid"), ("buy", "middle"))
        for side, reference in invalid:
            with self.subTest(side=side, reference=reference):
                with self.assertRaises(ValidationError):
                    engine.place_pegged(side, reference, 5)

        self.assertEqual(
            engine.place_pegged("buy", "bid", 5).order_id,
            "order_000003",
        )

    def test_snapshot_contains_peg_metadata_and_peg_is_executable(self):
        engine = MatchingEngine()
        engine.place_limit("buy", "10.10", 10)
        peg = engine.place_pegged("buy", "bid", 5)

        entry = engine.snapshot().bids[1]
        self.assertEqual(entry.order_id, peg.order_id)
        self.assertIs(entry.order_type, OrderType.PEGGED)
        self.assertIs(entry.peg_reference, PegReference.BID)
        self.assertEqual(entry.price, Decimal("10.10"))

        result = engine.place_market("sell", 15)
        self.assertEqual(result.trades, (Trade(Decimal("10.10"), 15),))
        self.assertEqual(engine.snapshot().bids, ())


class PeggedReferenceLifecycleTests(unittest.TestCase):
    def test_bid_peg_reprices_up_and_down_while_preserving_sequence(self):
        engine = MatchingEngine()
        base = engine.place_limit("buy", "10", 10)
        peg = engine.place_pegged("buy", "bid", 10)
        lower = engine.place_limit("buy", "9", 10)
        higher = engine.place_limit("buy", "11", 10)

        self.assertEqual(
            [entry.order_id for entry in engine.snapshot().bids[:2]],
            [peg.order_id, higher.order_id],
        )

        engine.cancel(higher.order_id)
        self.assertEqual(engine.snapshot().bids[0].order_id, base.order_id)
        engine.cancel(base.order_id)
        self.assertEqual(
            [entry.order_id for entry in engine.snapshot().bids],
            [peg.order_id, lower.order_id],
        )
        self.assertEqual(engine.snapshot().bids[0].price, Decimal("9"))

    def test_pegs_are_excluded_from_reference_and_retain_last_price(self):
        engine = MatchingEngine()
        reference = engine.place_limit("buy", "10", 10)
        retained = engine.place_pegged("buy", "bid", 10)
        engine.cancel(reference.order_id)

        entry = engine.snapshot().bids[0]
        self.assertEqual((entry.order_id, entry.price), (retained.order_id, Decimal("10")))
        with self.assertRaises(MissingReferenceError):
            engine.place_pegged("buy", "bid", 1)

    def test_retained_peg_reprices_when_regular_reference_reappears(self):
        engine = MatchingEngine()
        reference = engine.place_limit("buy", "10", 10)
        peg = engine.place_pegged("buy", "bid", 10)
        engine.cancel(reference.order_id)

        new_reference = engine.place_limit("buy", "8.75", 10)

        self.assertEqual(
            [(entry.order_id, entry.price) for entry in engine.snapshot().bids],
            [(peg.order_id, Decimal("8.75")), (new_reference.order_id, Decimal("8.75"))],
        )

    def test_market_sweep_reprices_peg_to_next_regular_bid(self):
        engine = MatchingEngine()
        engine.place_limit("buy", "10", 10)
        peg = engine.place_pegged("buy", "bid", 10)
        lower = engine.place_limit("buy", "9", 10)

        result = engine.place_market("sell", 25)

        self.assertEqual(
            result.trades,
            (Trade(Decimal("10"), 10), Trade(Decimal("9"), 15)),
        )
        self.assertEqual(
            [(entry.order_id, entry.quantity) for entry in engine.snapshot().bids],
            [(lower.order_id, 5)],
        )
        self.assertIsNotNone(peg.order_id)

    def test_market_sweep_uses_retained_price_when_last_reference_disappears(self):
        engine = MatchingEngine()
        engine.place_limit("buy", "10", 10)
        peg = engine.place_pegged("buy", "bid", 10)

        result = engine.place_market("sell", 15)

        self.assertEqual(result.trades, (Trade(Decimal("10"), 15),))
        remaining = engine.snapshot().bids[0]
        self.assertEqual((remaining.order_id, remaining.quantity), (peg.order_id, 5))

    def test_offer_pegs_follow_exact_decimal_reference(self):
        engine = MatchingEngine()
        engine.place_limit("sell", "10.10", 10)
        peg = engine.place_pegged("sell", "offer", 7)

        new_offer = engine.place_limit("sell", "9.90", 4)

        offers = engine.snapshot().offers
        self.assertEqual(
            [(entry.order_id, entry.price) for entry in offers[:2]],
            [(peg.order_id, Decimal("9.90")), (new_offer.order_id, Decimal("9.90"))],
        )
        self.assertIs(offers[0].peg_reference, PegReference.OFFER)


class PeggedCancellationAndAmendmentTests(unittest.TestCase):
    def test_peg_and_regular_reference_can_be_cancelled(self):
        engine = MatchingEngine()
        reference = engine.place_limit("sell", "11", 10)
        peg = engine.place_pegged("sell", "offer", 10)

        engine.cancel(peg.order_id)
        self.assertEqual(
            [entry.order_id for entry in engine.snapshot().offers],
            [reference.order_id],
        )
        with self.assertRaises(UnknownOrderError):
            engine.cancel(peg.order_id)

        engine.cancel(reference.order_id)
        self.assertEqual(engine.snapshot().offers, ())

    def test_regular_price_amendment_changes_peg_reference(self):
        engine = MatchingEngine()
        reference = engine.place_limit("buy", "10", 10)
        peg = engine.place_pegged("buy", "bid", 10)

        engine.amend(reference.order_id, price="10.25")

        self.assertEqual(
            [(entry.order_id, entry.price) for entry in engine.snapshot().bids],
            [(peg.order_id, Decimal("10.25")), (reference.order_id, Decimal("10.25"))],
        )

    def test_peg_quantity_reduction_preserves_priority(self):
        engine = MatchingEngine()
        reference = engine.place_limit("buy", "10", 10)
        first = engine.place_pegged("buy", "bid", 10)
        second = engine.place_pegged("buy", "bid", 10)
        engine.cancel(reference.order_id)

        engine.amend(first.order_id, quantity=5)
        engine.place_market("sell", 6)

        self.assertEqual(
            [(entry.order_id, entry.quantity) for entry in engine.snapshot().bids],
            [(second.order_id, 9)],
        )

    def test_peg_quantity_increase_loses_priority(self):
        engine = MatchingEngine()
        reference = engine.place_limit("buy", "10", 10)
        first = engine.place_pegged("buy", "bid", 10)
        second = engine.place_pegged("buy", "bid", 10)
        engine.cancel(reference.order_id)

        engine.amend(first.order_id, quantity=20)
        engine.place_market("sell", 11)

        self.assertEqual(
            [(entry.order_id, entry.quantity) for entry in engine.snapshot().bids],
            [(first.order_id, 19)],
        )
        self.assertNotEqual(first.order_id, second.order_id)

    def test_same_quantity_is_no_op_and_direct_price_rejection_is_atomic(self):
        engine = MatchingEngine()
        engine.place_limit("buy", "10", 10)
        first = engine.place_pegged("buy", "bid", 10)
        second = engine.place_pegged("buy", "bid", 10)

        engine.amend(first.order_id, quantity=10)
        with self.assertRaises(ValidationError):
            engine.amend(first.order_id, price="11", quantity=20)

        entries = engine.snapshot().bids
        self.assertEqual(
            [(entry.order_id, entry.quantity) for entry in entries[1:]],
            [(first.order_id, 10), (second.order_id, 10)],
        )


class PeggedOrderBookPrimitiveTests(unittest.TestCase):
    def test_best_regular_excludes_pegs_and_cleans_removed_entries(self):
        book = LimitOrderBook()
        regular = book.rest(RestingOrder("regular", "buy", "10", 10))
        peg = book.rest(
            RestingOrder(
                "peg",
                "buy",
                "11",
                10,
                OrderType.PEGGED,
                PegReference.BID,
            )
        )

        self.assertIs(book.best(Side.BUY), peg)
        self.assertIs(book.best_regular(Side.BUY), regular)
        book.remove(regular.order_id)
        self.assertIsNone(book.best_regular(Side.BUY))

    def test_reprice_preserves_sequence_and_lazily_cleans_old_level(self):
        book = LimitOrderBook()
        book.rest(RestingOrder("anchor", "buy", "10", 10))
        peg = book.rest(
            RestingOrder(
                "peg",
                "buy",
                "10",
                10,
                OrderType.PEGGED,
                PegReference.BID,
            )
        )
        newer = book.rest(RestingOrder("newer", "buy", "11.00", 10))
        sequence = peg.sequence

        book.reprice(peg.order_id, Decimal("11.00"))

        self.assertEqual(peg.sequence, sequence)
        self.assertIs(book.best_bid(), peg)
        book.remove(peg.order_id)
        self.assertIs(book.best_bid(), newer)
        self.assertEqual(
            [entry.order_id for entry in book.snapshot().bids],
            [newer.order_id, "anchor"],
        )

    def test_resting_order_rejects_market_and_invalid_peg_metadata(self):
        with self.assertRaises(ValidationError):
            RestingOrder("market", "buy", "10", 1, OrderType.MARKET)
        with self.assertRaises(ValidationError):
            RestingOrder("peg", "buy", "10", 1, OrderType.PEGGED)
        with self.assertRaises(ValidationError):
            RestingOrder(
                "wrong",
                "buy",
                "10",
                1,
                OrderType.PEGGED,
                PegReference.OFFER,
            )


class PeggedInvariantTests(unittest.TestCase):
    def test_seeded_lifecycle_keeps_pegs_on_regular_best(self):
        rng = Random(20260904)
        engine = MatchingEngine()
        regular_ids = {
            Side.BUY: [engine.place_limit("buy", "5", 10).order_id],
            Side.SELL: [engine.place_limit("sell", "15", 10).order_id],
        }
        peg_ids = {
            Side.BUY: engine.place_pegged("buy", "bid", 10).order_id,
            Side.SELL: engine.place_pegged("sell", "offer", 10).order_id,
        }

        for _ in range(80):
            side = rng.choice((Side.BUY, Side.SELL))
            action = rng.choice(("insert", "amend", "cancel", "peg_qty"))
            prices = range(1, 10) if side is Side.BUY else range(11, 20)

            if action == "insert":
                result = engine.place_limit(side, str(rng.choice(prices)), rng.randint(1, 20))
                regular_ids[side].append(result.order_id)
            elif action == "amend":
                order_id = rng.choice(regular_ids[side])
                engine.amend(
                    order_id,
                    price=str(rng.choice(prices)),
                    quantity=rng.randint(1, 20),
                )
            elif action == "cancel" and len(regular_ids[side]) > 1:
                order_id = rng.choice(regular_ids[side])
                engine.cancel(order_id)
                regular_ids[side].remove(order_id)
            else:
                engine.amend(peg_ids[side], quantity=rng.randint(1, 20))

            snapshot = engine.snapshot()
            for checked_side, entries in (
                (Side.BUY, snapshot.bids),
                (Side.SELL, snapshot.offers),
            ):
                regular = [
                    entry for entry in entries if entry.order_type is OrderType.LIMIT
                ]
                pegs = [
                    entry for entry in entries if entry.order_type is OrderType.PEGGED
                ]
                best_price = (
                    max(entry.price for entry in regular)
                    if checked_side is Side.BUY
                    else min(entry.price for entry in regular)
                )
                self.assertTrue(all(entry.price == best_price for entry in pegs))
                prices_in_book = [entry.price for entry in entries]
                self.assertEqual(
                    prices_in_book,
                    sorted(prices_in_book, reverse=checked_side is Side.BUY),
                )


if __name__ == "__main__":
    unittest.main()
