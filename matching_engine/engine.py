"""Order submission and matching policies for the single-asset engine."""

from __future__ import annotations

from decimal import Decimal

from .exceptions import MissingReferenceError, ValidationError
from .models import (
    BookSnapshot,
    OperationResult,
    OrderType,
    PegReference,
    Side,
    Trade,
    _decimal_price,
    _peg_reference,
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
        self._next_order_id = 1
        self._pegged_ids: dict[PegReference, set[str]] = {
            PegReference.BID: set(),
            PegReference.OFFER: set(),
        }

    def place_limit(
        self, side: Side | str, price: Decimal | str | int, quantity: int
    ) -> OperationResult:
        """Submit a limit order, matching first and resting any remainder."""

        normalized_side = _side(side)
        normalized_price = _decimal_price(price)
        normalized_quantity = _quantity(quantity)

        order_id = self._new_order_id_value()
        remaining, trades = self._match(
            normalized_side,
            normalized_quantity,
            limit_price=normalized_price,
        )
        if remaining:
            self._rest_regular(
                order_id, normalized_side, normalized_price, remaining
            )
        return OperationResult(order_id=order_id, trades=trades)

    def place_market(self, side: Side | str, quantity: int) -> OperationResult:
        """Execute immediately against available liquidity and discard excess."""

        normalized_side = _side(side)
        normalized_quantity = _quantity(quantity)
        _, trades = self._match(normalized_side, normalized_quantity)
        return OperationResult(order_id=None, trades=trades)

    def place_pegged(
        self,
        side: Side | str,
        reference: PegReference | str,
        quantity: int,
    ) -> OperationResult:
        """Rest a same-side peg at its current regular reference price."""

        normalized_side = _side(side)
        normalized_reference = _peg_reference(reference)
        normalized_quantity = _quantity(quantity)
        expected_reference = (
            PegReference.BID
            if normalized_side is Side.BUY
            else PegReference.OFFER
        )
        if normalized_reference is not expected_reference:
            raise ValidationError("buy pegs must reference bid and sell pegs offer")

        regular = self._book.best_regular(normalized_side)
        if regular is None:
            raise MissingReferenceError(
                f"no regular {normalized_reference.value} reference is available"
            )

        order_id = self._new_order_id_value()
        self._book.rest(
            RestingOrder(
                order_id=order_id,
                side=normalized_side,
                price=regular.price,
                quantity=normalized_quantity,
                order_type=OrderType.PEGGED,
                peg_reference=normalized_reference,
            )
        )
        self._pegged_ids[normalized_reference].add(order_id)
        return OperationResult(order_id=order_id)

    def cancel(self, order_id: str) -> None:
        """Cancel a resting order.

        Filled, previously cancelled, and otherwise unknown IDs all raise
        :class:`UnknownOrderError` through the order book's ID registry.
        """

        self._validate_order_id(order_id)
        order = self._book.remove(order_id)
        if order.order_type is OrderType.PEGGED:
            self._pegged_ids[order.peg_reference].discard(order_id)
        else:
            self._sync_reference_for_side(order.side)

    def amend(
        self,
        order_id: str,
        *,
        price: Decimal | str | int | None = None,
        quantity: int | None = None,
    ) -> OperationResult:
        """Amend a regular order's price/quantity or a peg's quantity.

        Quantity is interpreted as the new remaining quantity.  Reductions at
        an unchanged price retain time priority; increases and effective price
        changes are handled as cancel-and-replace and receive new priority.
        """

        self._validate_order_id(order_id)
        order = self._book.require(order_id)
        if price is None and quantity is None:
            raise ValidationError("amendment requires price, quantity, or both")

        if order.order_type is OrderType.PEGGED:
            return self._amend_pegged(order, price=price, quantity=quantity)

        # Normalize every supplied field before changing state so a rejected
        # amendment cannot partially modify or remove the existing order.
        new_price = order.price if price is None else _decimal_price(price)
        new_quantity = order.quantity if quantity is None else _quantity(quantity)

        price_changed = new_price != order.price
        quantity_increased = new_quantity > order.quantity

        if not price_changed and not quantity_increased:
            if new_quantity < order.quantity:
                self._book.reduce(order_id, new_quantity)
            self._sync_reference_for_side(order.side)
            return OperationResult(order_id=order_id)

        side = order.side
        self._book.remove(order_id)
        self._sync_reference_for_side(side)
        remaining, trades = self._match(side, new_quantity, limit_price=new_price)
        if remaining:
            self._rest_regular(order_id, side, new_price, remaining)
        return OperationResult(order_id=order_id, trades=trades)

    def snapshot(self) -> BookSnapshot:
        """Return the book's immutable current view."""

        return self._book.snapshot()

    def _new_order_id_value(self) -> str:
        order_id = f"order_{self._next_order_id:06d}"
        self._next_order_id += 1
        return order_id

    def _rest_regular(
        self, order_id: str, side: Side, price: Decimal, quantity: int
    ) -> None:
        self._book.rest(
            RestingOrder(
                order_id=order_id,
                side=side,
                price=price,
                quantity=quantity,
            )
        )
        self._sync_reference_for_side(side)

    def _amend_pegged(
        self,
        order: RestingOrder,
        *,
        price: Decimal | str | int | None,
        quantity: int | None,
    ) -> OperationResult:
        if price is not None:
            raise ValidationError("a pegged order's price cannot be amended directly")
        if quantity is None:
            raise ValidationError("pegged amendments require quantity")
        new_quantity = _quantity(quantity)

        if new_quantity < order.quantity:
            self._book.reduce(order.order_id, new_quantity)
        elif new_quantity > order.quantity:
            self._book.remove(order.order_id)
            self._book.rest(
                RestingOrder(
                    order_id=order.order_id,
                    side=order.side,
                    price=order.price,
                    quantity=new_quantity,
                    order_type=OrderType.PEGGED,
                    peg_reference=order.peg_reference,
                )
            )
        return OperationResult(order_id=order.order_id)

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
            maker_type = maker.order_type
            maker_reference = maker.peg_reference
            maker_side = maker.side
            fill_quantity = min(remaining, maker.quantity)
            remaining -= fill_quantity
            self._book.consume(maker.order_id, fill_quantity)
            self._record_trade(trades, maker_price, fill_quantity)
            if self._book.get(maker.order_id) is None:
                if maker_type is OrderType.PEGGED:
                    self._pegged_ids[maker_reference].discard(maker.order_id)
                else:
                    self._sync_reference_for_side(maker_side)
            elif maker_type is OrderType.LIMIT:
                self._sync_reference_for_side(maker_side)

        return remaining, tuple(trades)

    def _sync_reference_for_side(self, side: Side) -> None:
        reference = PegReference.BID if side is Side.BUY else PegReference.OFFER
        regular = self._book.best_regular(side)
        if regular is None:
            return

        active_pegs: list[RestingOrder] = []
        stale_ids: list[str] = []
        for order_id in self._pegged_ids[reference]:
            order = self._book.get(order_id)
            if order is None:
                stale_ids.append(order_id)
            else:
                active_pegs.append(order)
        for order_id in stale_ids:
            self._pegged_ids[reference].discard(order_id)
        for order in sorted(active_pegs, key=lambda value: value.sequence):
            self._book.reprice(order.order_id, regular.price)

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
