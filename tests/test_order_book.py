import unittest
from decimal import Decimal

from matching_engine import Side, ValidationError
from matching_engine.exceptions import UnknownOrderError
from matching_engine.order_book import LimitOrderBook, RestingOrder


def order(order_id, side, price, quantity):
    return RestingOrder(order_id, side, Decimal(price), quantity)


class LimitOrderBookTests(unittest.TestCase):
    def test_best_bid_and_offer_use_price_priority(self):
        book = LimitOrderBook()
        low_bid = book.rest(order("b1", Side.BUY, "9.99", 10))
        high_bid = book.rest(order("b2", Side.BUY, "10.00", 10))
        high_offer = book.rest(order("s1", Side.SELL, "10.50", 10))
        low_offer = book.rest(order("s2", Side.SELL, "10.10", 10))

        self.assertIs(book.best_bid(), high_bid)
        self.assertIs(book.best_offer(), low_offer)
        self.assertIsNot(book.best_bid(), low_bid)
        self.assertIsNot(book.best_offer(), high_offer)

    def test_equal_price_orders_are_fifo(self):
        book = LimitOrderBook()
        first = book.rest(order("b1", Side.BUY, "10", 100))
        second = book.rest(order("b2", Side.BUY, "10.0", 200))

        self.assertIs(book.best_bid(), first)
        self.assertLess(first.sequence, second.sequence)

    def test_snapshot_sorts_each_side_and_is_immutable(self):
        book = LimitOrderBook()
        book.rest(order("b-old", Side.BUY, "9.99", 5))
        book.rest(order("b-new", Side.BUY, "10", 6))
        book.rest(order("b-same", Side.BUY, "10.00", 7))
        book.rest(order("s-old", Side.SELL, "10.50", 8))
        book.rest(order("s-new", Side.SELL, "10.10", 9))

        snapshot = book.snapshot()
        self.assertEqual(
            [entry.order_id for entry in snapshot.bids],
            ["b-new", "b-same", "b-old"],
        )
        self.assertEqual(
            [entry.order_id for entry in snapshot.offers],
            ["s-new", "s-old"],
        )
        self.assertIsInstance(snapshot.bids, tuple)
        self.assertEqual(snapshot.bids[0].price, Decimal("10"))

    def test_remove_is_logical_and_lazy_heap_cleanup_keeps_next_order(self):
        book = LimitOrderBook()
        first = book.rest(order("b1", Side.BUY, "10", 100))
        second = book.rest(order("b2", Side.BUY, "10", 200))

        removed = book.remove(first.order_id)
        self.assertIs(removed, first)
        self.assertFalse(first.active)
        self.assertIsNone(book.get(first.order_id))
        self.assertIs(book.best_bid(), second)
        self.assertEqual(book.snapshot().bids[0].order_id, second.order_id)

    def test_duplicate_and_unknown_ids_are_rejected(self):
        book = LimitOrderBook()
        book.rest(order("same", Side.BUY, "10", 1))
        with self.assertRaises(ValidationError):
            book.rest(order("same", Side.SELL, "11", 1))
        with self.assertRaises(UnknownOrderError):
            book.remove("missing")
        with self.assertRaises(UnknownOrderError):
            book.require("missing")

    def test_decimal_prices_are_exact_and_not_float_keys(self):
        book = LimitOrderBook()
        decimal_order = book.rest(order("b1", Side.BUY, "0.3", 1))
        self.assertEqual(decimal_order.price, Decimal("0.3"))
        self.assertIs(book.best_bid(), decimal_order)
        self.assertEqual(book.snapshot().bids[0].price, Decimal("0.3"))

    def test_only_resting_order_values_can_be_inserted(self):
        with self.assertRaises(ValidationError):
            LimitOrderBook().rest(object())


if __name__ == "__main__":
    unittest.main()
