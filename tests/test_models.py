import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal

from matching_engine import (
    BookEntry,
    BookSnapshot,
    MissingReferenceError,
    OperationResult,
    OrderType,
    PegReference,
    Side,
    Trade,
    ValidationError,
)


class EnumTests(unittest.TestCase):
    def test_enum_values_are_cli_friendly(self):
        self.assertEqual(Side("buy"), Side.BUY)
        self.assertEqual(OrderType("market"), OrderType.MARKET)
        self.assertEqual(PegReference("offer"), PegReference.OFFER)
        self.assertIs(Side.BUY.opposite, Side.SELL)


class ValueTypeTests(unittest.TestCase):
    def test_trade_normalizes_price_and_is_immutable(self):
        trade = Trade("10.10", 4)
        self.assertEqual(trade.price, Decimal("10.10"))
        self.assertEqual(trade.quantity, 4)
        with self.assertRaises(FrozenInstanceError):
            trade.qty = 5

    def test_operation_result_normalizes_trade_collection(self):
        result = OperationResult("order_000001", [Trade(Decimal("10"), 2)])
        self.assertEqual(result.created_order_id, "order_000001")
        self.assertIsInstance(result.trades, tuple)

    def test_book_entry_accepts_string_enum_values(self):
        entry = BookEntry("order_000001", "buy", "10", 2)
        self.assertEqual(entry.side, Side.BUY)
        self.assertEqual(entry.order_type, OrderType.LIMIT)
        self.assertEqual(entry.qty, 2)

    def test_pegged_entry_requires_reference(self):
        with self.assertRaises(ValidationError):
            BookEntry("order_000001", Side.BUY, Decimal("10"), 2, OrderType.PEGGED)

    def test_regular_entry_cannot_have_reference(self):
        with self.assertRaises(ValidationError):
            BookEntry(
                "order_000001",
                Side.BUY,
                Decimal("10"),
                2,
                OrderType.LIMIT,
                PegReference.BID,
            )

    def test_snapshot_is_immutable_and_checks_sides(self):
        bid = BookEntry("order_000001", Side.BUY, Decimal("10"), 2)
        snapshot = BookSnapshot([bid], [])
        self.assertEqual(snapshot.bids, (bid,))
        with self.assertRaises(FrozenInstanceError):
            snapshot.bids = ()
        with self.assertRaises(ValidationError):
            BookSnapshot([], [bid])

    def test_invalid_values_raise_validation_error(self):
        invalid_values = [0, -1, True, 1.5]
        for quantity in invalid_values:
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValidationError):
                    Trade(Decimal("10"), quantity)
        for price in [0, -1, Decimal("NaN"), Decimal("Infinity")]:
            with self.subTest(price=price):
                with self.assertRaises(ValidationError):
                    Trade(price, 1)


class ExceptionTests(unittest.TestCase):
    def test_missing_reference_is_validation_error(self):
        self.assertTrue(issubclass(MissingReferenceError, ValidationError))


if __name__ == "__main__":
    unittest.main()
