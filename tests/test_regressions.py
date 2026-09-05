"""Regressions for numeric exactness and reference-change optimization."""

import unittest
from decimal import Decimal, Inexact, Rounded, localcontext
from unittest.mock import patch

from matching_engine import MatchingEngine, PegReference, Side, Trade, ValidationError
from matching_engine.cli import CommandProcessor
from matching_engine.order_book import LimitOrderBook, RestingOrder


class FloatPriceRegressions(unittest.TestCase):
    def test_float_validation_is_context_independent(self):
        accepted = (
            (0.01, "0.01"), (10.25, "10.25"), (1e20, "1e20"),
            (0.001, "0.001"), (10.251, "10.251"),
            (0.1 + 0.2, "0.30000000000000004"),
        )
        rejected = (float("nan"), float("inf"),
                    float("-inf"), 0.0, -1.0, True)
        with localcontext() as context:
            context.prec = 1
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            for value, expected in accepted:
                with self.subTest(value=value):
                    self.assertEqual(Trade(value, 1).price, Decimal(expected))
            for value in rejected:
                with self.subTest(value=value):
                    with self.assertRaises(ValidationError):
                        Trade(value, 1)

    def test_rejected_float_placement_keeps_book_and_id_sequence(self):
        engine = MatchingEngine()
        engine.place_limit("sell", "0.2", 10)
        before = engine.snapshot()
        for price in (float("inf"), float("nan"), -0.001):
            with self.subTest(price=price):
                with self.assertRaises(ValidationError):
                    engine.place_limit("buy", price, 10)
                self.assertEqual(engine.snapshot(), before)
        self.assertEqual(engine.place_limit("buy", 0.1, 1).order_id, "order_000002")

    def test_rejected_amendment_preserves_quantity_priority_and_pegs(self):
        engine = MatchingEngine()
        order_id = engine.place_limit("buy", 10.25, 10).order_id
        engine.place_pegged("buy", "bid", 2)
        before = engine.snapshot()
        order = engine._book.require(order_id)
        tokens = (order.sequence, order.generation)
        for fields in ({"price": float("inf"), "quantity": 50},
                       {"price": 10.251, "quantity": 0}):
            with self.subTest(fields=fields):
                with self.assertRaises(ValidationError):
                    engine.amend(order_id, **fields)
                self.assertEqual(engine.snapshot(), before)
                self.assertEqual((order.sequence, order.generation), tokens)
                self.assertEqual(
                    engine._last_reference_prices[PegReference.BID], Decimal("10.25")
                )

    def test_prices_keep_more_than_two_decimal_places(self):
        processor = CommandProcessor()
        processor.execute("limit buy 10.251 2")
        self.assertEqual(processor.engine.snapshot().bids[0].price, Decimal("10.251"))
        result = processor.engine.amend("order_000001", price=10.2517)
        self.assertEqual(result.trades, ())
        self.assertEqual(processor.engine.snapshot().bids[0].price, Decimal("10.2517"))
        processor.engine.amend("order_000001", price=Decimal("10.25171"))
        self.assertEqual(processor.engine.snapshot().bids[0].price, Decimal("10.25171"))


class DecimalOrderingRegressions(unittest.TestCase):
    LOW = Decimal("1.0000000000000000000000000001")
    HIGH = Decimal("1.0000000000000000000000000002")
    HIGHER = Decimal("1.0000000000000000000000000003")

    def test_market_and_crossing_limits_select_exact_best_bid(self):
        for precision in (2, 28):
            for kind in ("market", "limit"):
                with self.subTest(precision=precision, kind=kind), localcontext() as ctx:
                    ctx.prec = precision
                    engine = MatchingEngine()
                    lower = engine.place_limit("buy", self.LOW, 1)
                    higher = engine.place_limit("buy", self.HIGH, 1)
                    self.assertEqual(
                        [entry.order_id for entry in engine.snapshot().bids],
                        [higher.order_id, lower.order_id],
                    )
                    result = (engine.place_market("sell", 1) if kind == "market"
                              else engine.place_limit("sell", self.HIGH, 1))
                    self.assertEqual(result.trades, (Trade(self.HIGH, 1),))
                    self.assertEqual(engine.snapshot().offers, ())
                    self.assertEqual(engine.snapshot().bids[0].order_id, lower.order_id)

    def test_peg_repricing_and_snapshot_ignore_rounding_traps(self):
        with localcontext() as ctx:
            ctx.prec = 2
            ctx.traps[Inexact] = True
            ctx.traps[Rounded] = True
            engine = MatchingEngine()
            engine.place_limit("buy", self.LOW, 2)
            higher = engine.place_limit("buy", self.HIGH, 2)
            peg = engine.place_pegged("buy", "bid", 2)
            self.assertEqual(engine._book.require(peg.order_id).price, self.HIGH)
            newest = engine.place_limit("buy", self.HIGHER, 2)
            self.assertEqual(
                [entry.order_id for entry in engine.snapshot().bids[:2]],
                [peg.order_id, newest.order_id],
            )
            engine.cancel(newest.order_id)
            self.assertEqual(engine._book.require(peg.order_id).price, self.HIGH)
            self.assertEqual(engine.snapshot().bids[0].order_id, higher.order_id)
            self.assertEqual(
                engine.place_market("sell", 3).trades,
                (Trade(self.HIGH, 2), Trade(self.LOW, 1)),
            )
            self.assertEqual(engine._book.require(peg.order_id).price, self.LOW)

    def test_extreme_exponents_can_be_placed_amended_and_filled(self):
        # No fixed-point formatting: these compact Decimal values have huge exponents.
        for price in (Decimal("1e1000000"), Decimal("1e-1000100")):
            for amend in (False, True):
                with self.subTest(price=str(price), amend=amend):
                    engine = MatchingEngine()
                    order_id = engine.place_limit("buy", "10" if amend else price, 2).order_id
                    if amend:
                        engine.amend(order_id, price=price)
                    self.assertEqual(engine.snapshot().bids[0].price, price)
                    self.assertEqual(engine.place_market("sell", 2).trades, (Trade(price, 2),))
                    self.assertIsNone(engine._book.get(order_id))

    def test_recreated_price_level_remains_executable(self):
        book = LimitOrderBook()
        book.rest(RestingOrder("first", Side.BUY, "10", 1))
        book.remove("first")
        self.assertIsNone(book.best_bid())
        book.rest(RestingOrder("second", Side.BUY, "10", 1))
        book.reprice("second", "9")
        book.reprice("second", "10")
        self.assertEqual(book.best_bid().order_id, "second")
        book.consume("second", 1)
        self.assertIsNone(book.best_bid())


class PegSynchronizationRegressions(unittest.TestCase):
    def test_unchanged_references_do_not_visit_pegs(self):
        for side, reference, worse in ((Side.BUY, "bid", "9"),
                                        (Side.SELL, "offer", "11")):
            with self.subTest(side=side):
                engine = MatchingEngine()
                first = engine.place_limit(side, "10", 10)
                engine.place_limit(side, "10", 10)
                pegs = [engine.place_pegged(side, reference, 2).order_id for _ in range(5)]
                with patch.object(engine._book, "reprice", wraps=engine._book.reprice) as moved:
                    lower_priority = engine.place_limit(side, worse, 1)
                    engine.cancel(lower_priority.order_id)
                    engine.place_market(side.opposite, 1)
                    engine.amend(first.order_id, quantity=8)
                    engine.amend(first.order_id, quantity=8)
                    engine.place_market(side.opposite, 8)
                    moved.assert_not_called()
                self.assertTrue(all(engine._book.require(oid).price == Decimal("10") for oid in pegs))

    def test_reference_changes_visit_pegs_and_preserve_fifo(self):
        for side, reference, better in ((Side.BUY, "bid", "11"),
                                         (Side.SELL, "offer", "9")):
            with self.subTest(side=side):
                engine = MatchingEngine()
                base = engine.place_limit(side, "10", 1)
                pegs = [engine.place_pegged(side, reference, 1).order_id for _ in range(3)]
                with patch.object(engine._book, "reprice", wraps=engine._book.reprice) as moved:
                    new_best = engine.place_limit(side, better, 1)
                    self.assertEqual(moved.call_count, len(pegs))
                engine.place_market(side.opposite, 2)
                remaining = engine.snapshot().bids if side is Side.BUY else engine.snapshot().offers
                self.assertEqual(
                    [entry.order_id for entry in remaining],
                    [pegs[2], new_best.order_id, base.order_id],
                )


if __name__ == "__main__":
    unittest.main()
