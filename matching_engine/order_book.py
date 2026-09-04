"""Price-time priority storage for resting limit and pegged orders.

This module intentionally stores orders only.  It does not decide whether an
incoming order is marketable and does not execute trades; those policies belong
to the matching engine layer built on top of :class:`LimitOrderBook`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import heapq
from typing import Iterator

from .exceptions import UnknownOrderError, ValidationError
from .models import (
    BookEntry,
    BookSnapshot,
    OrderType,
    PegReference,
    Side,
    _decimal_price,
    _order_type,
    _peg_reference,
    _quantity,
    _side,
)


@dataclass(slots=True)
class RestingOrder:
    """Mutable internal representation of resting executable liquidity.

    ``sequence`` is assigned by the book and is never reused.  ``generation``
    invalidates old queue entries when the order is removed or repriced while
    allowing an automatic peg move to retain the original time priority.
    """

    order_id: str
    side: Side
    price: Decimal
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    peg_reference: PegReference | None = None
    sequence: int = field(default=0, init=False)
    generation: int = field(default=0, init=False)
    active: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValidationError("order_id must be a non-empty string")
        self.side = _side(self.side)
        self.price = _decimal_price(self.price)
        self.quantity = _quantity(self.quantity)
        self.order_type = _order_type(self.order_type)
        self.peg_reference = _peg_reference(self.peg_reference)
        if self.order_type is OrderType.MARKET:
            raise ValidationError("market orders cannot rest in the book")
        if self.order_type is OrderType.PEGGED:
            if self.peg_reference is None:
                raise ValidationError("pegged orders require a peg reference")
            expected_reference = (
                PegReference.BID if self.side is Side.BUY else PegReference.OFFER
            )
            if self.peg_reference is not expected_reference:
                raise ValidationError("buy pegs must reference bid and sell pegs offer")
        elif self.peg_reference is not None:
            raise ValidationError("only pegged orders may have a peg reference")

    @property
    def remaining_quantity(self) -> int:
        """Alias used by matching code when consuming a resting order."""

        return self.quantity

    def as_book_entry(self) -> BookEntry:
        """Create an immutable public representation of this order."""

        return BookEntry(
            order_id=self.order_id,
            side=self.side,
            price=self.price,
            quantity=self.quantity,
            order_type=self.order_type,
            peg_reference=self.peg_reference,
        )


@dataclass(slots=True)
class _PriceLevel:
    """A FIFO priority heap and identity token for one side/price pair."""

    generation: int
    entries: list[tuple[int, int, str]] = field(default_factory=list)


class LimitOrderBook:
    """In-memory resting order book with price-time ordering.

    The order ID index makes lookup and logical removal O(1).  Price and queue
    heaps use lazy invalidation: stale entries are discarded only when they
    reach the relevant heap's head, keeping future repricing and cancellation
    operations independent of queue length.
    """

    def __init__(self) -> None:
        self._orders: dict[str, RestingOrder] = {}
        self._levels: dict[tuple[Side, Decimal], _PriceLevel] = {}
        self._price_heaps: dict[Side, list[tuple[Decimal, int, Decimal]]] = {
            Side.BUY: [],
            Side.SELL: [],
        }
        self._regular_heaps: dict[
            Side, list[tuple[Decimal, int, int, str, Decimal]]
        ] = {
            Side.BUY: [],
            Side.SELL: [],
        }
        self._next_sequence = 1
        self._next_level_generation = 1

    def rest(self, order: RestingOrder) -> RestingOrder:
        """Insert an order and assign it the next FIFO priority sequence."""

        if not isinstance(order, RestingOrder):
            raise ValidationError("only RestingOrder values can be inserted")
        if order.order_id in self._orders:
            raise ValidationError(f"duplicate order ID: {order.order_id}")
        if not order.active:
            raise ValidationError("an inactive order cannot be inserted")

        order.sequence = self._next_sequence
        self._next_sequence += 1
        order.generation = 0

        key = (order.side, order.price)
        level = self._levels.get(key)
        if level is None:
            level = _PriceLevel(self._next_level_generation)
            self._next_level_generation += 1
            self._levels[key] = level
            price_key = order.price if order.side is Side.SELL else -order.price
            heapq.heappush(
                self._price_heaps[order.side],
                (price_key, level.generation, order.price),
            )

        heapq.heappush(level.entries, (order.sequence, order.generation, order.order_id))
        self._orders[order.order_id] = order
        if order.order_type is OrderType.LIMIT:
            self._push_regular(order)
        return order

    # ``insert`` makes the primitive self-documenting for callers that think
    # in terms of data structures rather than exchange terminology.
    insert = rest

    def get(self, order_id: str) -> RestingOrder | None:
        """Return an active order by ID, or ``None`` when it is not present."""

        return self._orders.get(order_id)

    def require(self, order_id: str) -> RestingOrder:
        """Return an active order or raise the domain-specific lookup error."""

        order = self.get(order_id)
        if order is None:
            raise UnknownOrderError(f"unknown order ID: {order_id}")
        return order

    def remove(self, order_id: str) -> RestingOrder:
        """Logically remove and return an order, leaving stale heap entries."""

        order = self.require(order_id)
        del self._orders[order_id]
        order.active = False
        order.generation += 1
        return order

    def consume(self, order_id: str, quantity: int) -> RestingOrder:
        """Apply an execution to an active maker order.

        Partial fills update the order in place so its time priority is
        unchanged.  A full fill uses logical removal, allowing the existing
        lazy heap cleanup to discard its stale queue entry later.
        """

        fill_quantity = _quantity(quantity)
        order = self.require(order_id)
        if fill_quantity > order.quantity:
            raise ValidationError("fill quantity exceeds remaining order quantity")
        order.quantity -= fill_quantity
        if order.quantity == 0:
            self.remove(order_id)
        return order

    def reduce(self, order_id: str, quantity: int) -> RestingOrder:
        """Reduce an order's remaining quantity without changing priority."""

        new_quantity = _quantity(quantity)
        order = self.require(order_id)
        if new_quantity > order.quantity:
            raise ValidationError("quantity reduction cannot increase an order")
        order.quantity = new_quantity
        return order

    def reprice(self, order_id: str, price: Decimal | str | int) -> RestingOrder:
        """Move an active order to another level while retaining its sequence."""

        new_price = _decimal_price(price)
        order = self.require(order_id)
        if new_price == order.price:
            return order

        order.generation += 1
        order.price = new_price
        key = (order.side, new_price)
        level = self._levels.get(key)
        if level is None:
            level = _PriceLevel(self._next_level_generation)
            self._next_level_generation += 1
            self._levels[key] = level
            price_key = new_price if order.side is Side.SELL else -new_price
            heapq.heappush(
                self._price_heaps[order.side],
                (price_key, level.generation, new_price),
            )
        heapq.heappush(level.entries, (order.sequence, order.generation, order_id))
        if order.order_type is OrderType.LIMIT:
            self._push_regular(order)
        return order

    def best(self, side: Side | str) -> RestingOrder | None:
        """Return the best active order for a side without executing it."""

        side = _side(side)
        prices = self._price_heaps[side]
        while prices:
            _, level_generation, price = prices[0]
            level = self._levels.get((side, price))
            if level is None or level.generation != level_generation:
                heapq.heappop(prices)
                continue

            order = self._clean_level(side, price, level)
            if order is not None:
                return order
            # The level was empty and was removed by _clean_level.
            heapq.heappop(prices)
        return None

    def best_bid(self) -> RestingOrder | None:
        """Return the highest-priced active bid."""

        return self.best(Side.BUY)

    def best_offer(self) -> RestingOrder | None:
        """Return the lowest-priced active offer."""

        return self.best(Side.SELL)

    def best_regular(self, side: Side | str) -> RestingOrder | None:
        """Return the best regular LIMIT order, excluding all pegged orders."""

        side = _side(side)
        regular_orders = self._regular_heaps[side]
        while regular_orders:
            _, sequence, generation, order_id, price = regular_orders[0]
            order = self._orders.get(order_id)
            if (
                order is None
                or not order.active
                or order.order_type is not OrderType.LIMIT
                or order.side is not side
                or order.price != price
                or order.sequence != sequence
                or order.generation != generation
            ):
                heapq.heappop(regular_orders)
                continue
            return order
        return None

    def active_orders(self, side: Side | str | None = None) -> Iterator[RestingOrder]:
        """Iterate active orders in book display order.

        This is primarily an internal integration primitive; callers needing a
        stable public view should use :meth:`snapshot`.
        """

        selected = self._orders.values()
        if side is not None:
            normalized_side = _side(side)
            selected = (order for order in selected if order.side is normalized_side)
        return iter(
            sorted(
                selected,
                key=lambda order: (
                    -order.price if order.side is Side.BUY else order.price,
                    order.sequence,
                ),
            )
        )

    def snapshot(self) -> BookSnapshot:
        """Return an immutable, individually ordered view of the book."""

        bids = tuple(order.as_book_entry() for order in self.active_orders(Side.BUY))
        offers = tuple(order.as_book_entry() for order in self.active_orders(Side.SELL))
        return BookSnapshot(bids=bids, offers=offers)

    def _push_regular(self, order: RestingOrder) -> None:
        price_key = order.price if order.side is Side.SELL else -order.price
        heapq.heappush(
            self._regular_heaps[order.side],
            (
                price_key,
                order.sequence,
                order.generation,
                order.order_id,
                order.price,
            ),
        )

    def _clean_level(
        self, side: Side, price: Decimal, level: _PriceLevel
    ) -> RestingOrder | None:
        while level.entries:
            sequence, generation, order_id = level.entries[0]
            order = self._orders.get(order_id)
            if (
                order is None
                or not order.active
                or order.side is not side
                or order.price != price
                or order.sequence != sequence
                or order.generation != generation
            ):
                heapq.heappop(level.entries)
                continue
            return order

        if self._levels.get((side, price)) is level:
            del self._levels[(side, price)]
        return None


__all__ = ["LimitOrderBook", "RestingOrder"]
