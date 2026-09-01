import unittest
from decimal import Decimal

from matching_engine import MatchingEngine, Side, Trade, ValidationError


class MatchingEngineExerciseExampleTests(unittest.TestCase):
    def test_complete_exercise_example(self):
        engine = MatchingEngine()
        engine.place_limit("buy", "10", 100)
        engine.place_limit("sell", "20", 100)
        engine.place_limit("sell", "20", 200)

        self.assertEqual(
            engine.place_market("buy", 150).trades,
            (Trade(Decimal("20"), 150),),
        )
        self.assertEqual(engine.snapshot().offers[0].quantity, 150)

        self.assertEqual(
            engine.place_market("buy", 200).trades,
            (Trade(Decimal("20"), 150),),
        )
        self.assertEqual(
            engine.place_market("sell", 200).trades,
            (Trade(Decimal("10"), 100),),
        )
        self.assertEqual(engine.snapshot().bids, ())
        self.assertEqual(engine.snapshot().offers, ())


class MarketMatchingTests(unittest.TestCase):
    def test_best_price_then_fifo_and_same_price_aggregation(self):
        engine = MatchingEngine()
        expensive = engine.place_limit(Side.SELL, "11", 25)
        first_best = engine.place_limit(Side.SELL, "10", 40)
        second_best = engine.place_limit(Side.SELL, "10.00", 60)

        result = engine.place_market(Side.BUY, 75)

        self.assertEqual(result.trades, (Trade(Decimal("10"), 75),))
        snapshot = engine.snapshot()
        self.assertEqual(
            [(entry.order_id, entry.quantity) for entry in snapshot.offers],
            [(second_best.order_id, 25), (expensive.order_id, 25)],
        )
        self.assertNotIn(first_best.order_id, [entry.order_id for entry in snapshot.offers])

    def test_sweep_reports_one_trade_per_price_in_execution_order(self):
        engine = MatchingEngine()
        engine.place_limit("sell", "10", 20)
        engine.place_limit("sell", "10.5", 30)
        engine.place_limit("sell", "11", 40)

        result = engine.place_market("buy", 65)

        self.assertEqual(
            result.trades,
            (
                Trade(Decimal("10"), 20),
                Trade(Decimal("10.5"), 30),
                Trade(Decimal("11"), 15),
            ),
        )
        self.assertEqual(engine.snapshot().offers[0].quantity, 25)

    def test_partial_fill_updates_maker_and_full_fill_removes_it(self):
        engine = MatchingEngine()
        maker = engine.place_limit("buy", "9.99", 100)

        engine.place_market("sell", 30)
        entry = engine.snapshot().bids[0]
        self.assertEqual((entry.order_id, entry.quantity), (maker.order_id, 70))

        engine.place_market("sell", 70)
        self.assertEqual(engine.snapshot().bids, ())

    def test_market_remainder_is_discarded_and_market_has_no_id(self):
        engine = MatchingEngine()
        engine.place_limit("sell", "12", 5)

        result = engine.place_market("buy", 100)

        self.assertIsNone(result.order_id)
        self.assertEqual(result.trades, (Trade(Decimal("12"), 5),))
        self.assertEqual(engine.snapshot().offers, ())

    def test_empty_book_market_order_is_a_no_op(self):
        result = MatchingEngine().place_market("buy", 10)
        self.assertIsNone(result.order_id)
        self.assertEqual(result.trades, ())


class CrossingLimitTests(unittest.TestCase):
    def test_crossing_buy_executes_at_offer_and_rests_residual(self):
        engine = MatchingEngine()
        engine.place_limit("sell", "10.50", 100)

        result = engine.place_limit("buy", "11", 150)

        self.assertEqual(result.trades, (Trade(Decimal("10.50"), 100),))
        self.assertEqual(
            [(entry.order_id, entry.price, entry.quantity) for entry in engine.snapshot().bids],
            [(result.order_id, Decimal("11"), 50)],
        )

    def test_crossing_sell_executes_at_bid_and_rests_residual(self):
        engine = MatchingEngine()
        engine.place_limit("buy", "10", 30)

        result = engine.place_limit("sell", "9.50", 50)

        self.assertEqual(result.trades, (Trade(Decimal("10"), 30),))
        self.assertEqual(
            [(entry.order_id, entry.price, entry.quantity) for entry in engine.snapshot().offers],
            [(result.order_id, Decimal("9.50"), 20)],
        )

    def test_fully_filled_limit_still_returns_its_id(self):
        engine = MatchingEngine()
        engine.place_limit("sell", "10", 10)
        result = engine.place_limit("buy", "10", 10)

        self.assertEqual(result.order_id, "order_000002")
        self.assertEqual(engine.snapshot().bids, ())
        self.assertEqual(engine.snapshot().offers, ())

    def test_limit_submission_leaves_book_uncrossed(self):
        engine = MatchingEngine()
        engine.place_limit("sell", "10", 10)
        engine.place_limit("sell", "13", 10)
        engine.place_limit("buy", "12", 15)

        snapshot = engine.snapshot()
        self.assertLess(snapshot.bids[0].price, snapshot.offers[0].price)


class ValidationAndIdentityTests(unittest.TestCase):
    def test_limit_ids_are_deterministic_and_market_orders_do_not_consume_them(self):
        engine = MatchingEngine()
        first = engine.place_limit("buy", "1.00", 1)
        engine.place_market("sell", 1)
        second = engine.place_limit("sell", "2.00", 1)

        self.assertEqual(first.order_id, "order_000001")
        self.assertEqual(second.order_id, "order_000002")

    def test_invalid_submission_does_not_consume_limit_id(self):
        engine = MatchingEngine()
        invalid_calls = (
            lambda: engine.place_limit("hold", "10", 1),
            lambda: engine.place_limit("buy", "NaN", 1),
            lambda: engine.place_limit("buy", "10", 0),
            lambda: engine.place_market("sell", -1),
        )
        for call in invalid_calls:
            with self.assertRaises(ValidationError):
                call()

        self.assertEqual(
            engine.place_limit("buy", "10.10", 1).order_id,
            "order_000001",
        )
        self.assertEqual(engine.snapshot().bids[0].price, Decimal("10.10"))


if __name__ == "__main__":
    unittest.main()
