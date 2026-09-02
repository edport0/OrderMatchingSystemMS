import unittest
from decimal import Decimal

from matching_engine import MatchingEngine, Trade, UnknownOrderError, ValidationError


class CancellationTests(unittest.TestCase):
    def test_cancel_removes_order_and_unknown_ids_are_rejected(self):
        engine = MatchingEngine()
        result = engine.place_limit("buy", "10", 100)

        self.assertIsNone(engine.cancel(result.order_id))
        self.assertEqual(engine.snapshot().bids, ())
        self.assertEqual(engine.place_market("sell", 100).trades, ())

        with self.assertRaises(UnknownOrderError):
            engine.cancel(result.order_id)
        with self.assertRaises(UnknownOrderError):
            engine.cancel("order_999999")

    def test_cancel_rejects_invalid_ids_without_changing_book(self):
        engine = MatchingEngine()
        result = engine.place_limit("sell", "11", 10)

        for invalid_id in ("", "   ", None, 1):
            with self.subTest(order_id=invalid_id):
                with self.assertRaises(ValidationError):
                    engine.cancel(invalid_id)

        self.assertEqual(engine.snapshot().offers[0].order_id, result.order_id)

    def test_filled_order_is_no_longer_cancellable(self):
        engine = MatchingEngine()
        result = engine.place_limit("buy", "10", 10)
        engine.place_market("sell", 10)

        with self.assertRaises(UnknownOrderError):
            engine.cancel(result.order_id)


class AmendmentTests(unittest.TestCase):
    def test_price_change_repositions_order_in_book(self):
        engine = MatchingEngine()
        amended = engine.place_limit("buy", "10", 200)
        engine.place_limit("buy", "9.99", 100)
        engine.place_limit("sell", "10.5", 100)

        result = engine.amend(amended.order_id, price="9.98")

        self.assertEqual(result.order_id, amended.order_id)
        self.assertEqual(result.trades, ())
        self.assertEqual(
            [(entry.quantity, entry.price) for entry in engine.snapshot().bids],
            [(100, Decimal("9.99")), (200, Decimal("9.98"))],
        )

    def test_price_change_loses_priority_at_destination_level(self):
        engine = MatchingEngine()
        moving = engine.place_limit("buy", "10", 50)
        existing = engine.place_limit("buy", "9", 100)

        engine.amend(moving.order_id, price="9")
        engine.place_market("sell", 110)

        self.assertEqual(
            [(entry.order_id, entry.quantity) for entry in engine.snapshot().bids],
            [(moving.order_id, 40)],
        )
        self.assertNotEqual(moving.order_id, existing.order_id)

    def test_quantity_reduction_preserves_priority(self):
        engine = MatchingEngine()
        first = engine.place_limit("buy", "10", 100)
        second = engine.place_limit("buy", "10", 100)

        engine.amend(first.order_id, quantity=50)
        engine.place_market("sell", 60)

        self.assertEqual(
            [(entry.order_id, entry.quantity) for entry in engine.snapshot().bids],
            [(second.order_id, 90)],
        )

    def test_quantity_increase_loses_priority(self):
        engine = MatchingEngine()
        first = engine.place_limit("buy", "10", 50)
        second = engine.place_limit("buy", "10", 100)

        engine.amend(first.order_id, quantity=150)
        engine.place_market("sell", 110)

        self.assertEqual(
            [(entry.order_id, entry.quantity) for entry in engine.snapshot().bids],
            [(first.order_id, 140)],
        )
        self.assertNotEqual(first.order_id, second.order_id)

    def test_amendment_quantity_is_new_remaining_quantity(self):
        engine = MatchingEngine()
        result = engine.place_limit("sell", "10", 100)
        engine.place_market("buy", 40)

        engine.amend(result.order_id, quantity=30)

        self.assertEqual(engine.snapshot().offers[0].quantity, 30)

    def test_marketable_price_amendment_executes_at_resting_price(self):
        engine = MatchingEngine()
        amended = engine.place_limit("buy", "9", 150)
        engine.place_limit("sell", "10", 100)

        result = engine.amend(amended.order_id, price="11")

        self.assertEqual(result.trades, (Trade(Decimal("10"), 100),))
        self.assertEqual(
            [(entry.order_id, entry.price, entry.quantity) for entry in engine.snapshot().bids],
            [(amended.order_id, Decimal("11"), 50)],
        )

    def test_fully_filled_amendment_keeps_result_id_but_not_active_order(self):
        engine = MatchingEngine()
        amended = engine.place_limit("sell", "12", 20)
        engine.place_limit("buy", "10", 20)

        result = engine.amend(amended.order_id, price="10")

        self.assertEqual(result.order_id, amended.order_id)
        self.assertEqual(result.trades, (Trade(Decimal("10"), 20),))
        self.assertEqual(engine.snapshot().bids, ())
        self.assertEqual(engine.snapshot().offers, ())
        with self.assertRaises(UnknownOrderError):
            engine.amend(amended.order_id, quantity=10)

    def test_no_effective_change_preserves_priority(self):
        engine = MatchingEngine()
        first = engine.place_limit("sell", "10.0", 20)
        second = engine.place_limit("sell", "10", 20)

        engine.amend(first.order_id, price="10.00", quantity=20)
        engine.place_market("buy", 25)

        self.assertEqual(
            [(entry.order_id, entry.quantity) for entry in engine.snapshot().offers],
            [(second.order_id, 15)],
        )

    def test_invalid_amendments_are_atomic(self):
        engine = MatchingEngine()
        result = engine.place_limit("buy", "10", 100)

        invalid_amendments = (
            {},
            {"quantity": 0},
            {"quantity": -1},
            {"price": "NaN"},
            {"price": "0"},
        )
        for fields in invalid_amendments:
            with self.subTest(fields=fields):
                with self.assertRaises(ValidationError):
                    engine.amend(result.order_id, **fields)
                entry = engine.snapshot().bids[0]
                self.assertEqual((entry.price, entry.quantity), (Decimal("10"), 100))

        with self.assertRaises(UnknownOrderError):
            engine.amend("order_999999", quantity=1)


if __name__ == "__main__":
    unittest.main()
