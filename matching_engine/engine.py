"""Order submission and matching policies for the single-asset engine."""

from __future__ import annotations

from decimal import Decimal

from .exceptions import ValidationError
from .models import (
    BookSnapshot,
    OperationResult,
    Side,
    Trade,
    _decimal_price,
    _quantity,
    _side,
)
from .order_book import LimitOrderBook, RestingOrder


class MatchingEngine:
    """Single-asset, in-memory matching engine.

    Incoming orders are aggressive takers while orders already in the book are
    makers.  Trades therefore execute at the resting maker's price.
    """

    def __init__(self) -> None:
        self._book = LimitOrderBook()
        self._next_limit_id = 1

    def place_limit(
        self, side: Side | str, price: Decimal | str | int, quantity: int
    ) -> OperationResult:
        """Submit a limit order, matching first and resting any remainder."""

        normalized_side = _side(side)
        normalized_price = _decimal_price(price)
        normalized_quantity = _quantity(quantity)

        order_id = self._new_limit_id()
        remaining, trades = self._match(
            normalized_side,
            normalized_quantity,
            limit_price=normalized_price,
        )
        if remaining:
            self._book.rest(
                RestingOrder(
                    order_id=order_id,
                    side=normalized_side,
                    price=normalized_price,
                    quantity=remaining,
                )
            )
        return OperationResult(order_id=order_id, trades=trades)

    def place_market(self, side: Side | str, quantity: int) -> OperationResult:
        """Execute immediately against available liquidity and discard excess."""

        normalized_side = _side(side)
        normalized_quantity = _quantity(quantity)
        _, trades = self._match(normalized_side, normalized_quantity)
        return OperationResult(order_id=None, trades=trades)

    def cancel(self, order_id: str) -> None:
        """Cancel a resting order.

        Filled, previously cancelled, and otherwise unknown IDs all raise
        :class:`UnknownOrderError` through the order book's ID registry.
        """

        self._validate_order_id(order_id)
        self._book.remove(order_id)

    def amend(
        self,
        order_id: str,
        *,
        price: Decimal | str | int | None = None,
        quantity: int | None = None,
    ) -> OperationResult:
        """Change a resting limit order's price, quantity, or both.

        Quantity is interpreted as the new remaining quantity.  Reductions at
        an unchanged price retain time priority; increases and effective price
        changes are handled as cancel-and-replace and receive new priority.
        """

        self._validate_order_id(order_id)
        order = self._book.require(order_id)
        if price is None and quantity is None:
            raise ValidationError("amendment requires price, quantity, or both")

        # Normalize every supplied field before changing state so a rejected
        # amendment cannot partially modify or remove the existing order.
        new_price = order.price if price is None else _decimal_price(price)
        new_quantity = order.quantity if quantity is None else _quantity(quantity)

        price_changed = new_price != order.price
        quantity_increased = new_quantity > order.quantity

        if not price_changed and not quantity_increased:
            if new_quantity < order.quantity:
                self._book.reduce(order_id, new_quantity)
            return OperationResult(order_id=order_id)

        side = order.side
        self._book.remove(order_id)
        remaining, trades = self._match(side, new_quantity, limit_price=new_price)
        if remaining:
            self._book.rest(
                RestingOrder(
                    order_id=order_id,
                    side=side,
                    price=new_price,
                    quantity=remaining,
                )
            )
        return OperationResult(order_id=order_id, trades=trades)

    def snapshot(self) -> BookSnapshot:
        """Return the book's immutable current view."""

        return self._book.snapshot()

    def _new_limit_id(self) -> str:
        order_id = f"order_{self._next_limit_id:06d}"
        self._next_limit_id += 1
        return order_id

    @staticmethod
    def _validate_order_id(order_id: str) -> None:
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValidationError("order_id must be a non-empty string")

    def _match(
        self,
        taker_side: Side,
        quantity: int,
        *,
        limit_price: Decimal | None = None,
    ) -> tuple[int, tuple[Trade, ...]]:
        remaining = quantity
        trades: list[Trade] = []

        while remaining:
            maker = self._book.best(taker_side.opposite)
            if maker is None or not self._is_compatible(
                taker_side, maker.price, limit_price
            ):
                break

            maker_price = maker.price
            fill_quantity = min(remaining, maker.quantity)
            remaining -= fill_quantity
            self._book.consume(maker.order_id, fill_quantity)
            self._record_trade(trades, maker_price, fill_quantity)

        return remaining, tuple(trades)

    @staticmethod
    def _is_compatible(
        taker_side: Side, maker_price: Decimal, limit_price: Decimal | None
    ) -> bool:
        if limit_price is None:
            return True
        if taker_side is Side.BUY:
            return limit_price >= maker_price
        return limit_price <= maker_price

    @staticmethod
    def _record_trade(trades: list[Trade], price: Decimal, quantity: int) -> None:
        if trades and trades[-1].price == price:
            previous = trades[-1]
            trades[-1] = Trade(price=price, qty=previous.qty + quantity)
        else:
            trades.append(Trade(price=price, qty=quantity))


__all__ = ["MatchingEngine"]
