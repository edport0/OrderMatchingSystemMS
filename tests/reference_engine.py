"""Independent list model for generated commands; input edge cases have separate tests."""

from dataclasses import dataclass
from decimal import Decimal


class ReferenceMissingReferenceError(ValueError):
    """Raised when a peg has no regular order to follow."""


@dataclass
class RefOrder:
    order_id: str
    side: str
    price: Decimal
    quantity: int
    peg: bool = False
    reference: str | None = None
    sequence: int = 0


class ReferenceEngine:
    """Intentionally simple order list; no production indexing is reused."""

    def __init__(self) -> None:
        self.orders: list[RefOrder] = []
        self.next_id = 1
        self.next_sequence = 1

    def _new_id(self) -> str:
        order_id = f"order_{self.next_id:06d}"
        self.next_id += 1
        return order_id

    def _rest(self, order: RefOrder) -> None:
        order.sequence = self.next_sequence
        self.next_sequence += 1
        self.orders.append(order)

    def ordered(self, side: str) -> list[RefOrder]:
        values = [order for order in self.orders if order.side == side]
        values.sort(key=lambda order: order.sequence)
        values.sort(key=lambda order: order.price, reverse=side == "buy")
        return values

    def best_regular(self, side: str) -> RefOrder | None:
        values = [order for order in self.orders if order.side == side and not order.peg]
        if not values:
            return None
        best = values[0]
        for order in values[1:]:
            better_price = order.price > best.price if side == "buy" else order.price < best.price
            if better_price or (order.price == best.price and order.sequence < best.sequence):
                best = order
        return best

    def _sync(self, side: str) -> None:
        reference = self.best_regular(side)
        if reference is None:
            return
        for order in self.orders:
            if order.side == side and order.peg:
                order.price = reference.price

    @staticmethod
    def _valid_quantity(quantity: int) -> None:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be positive")

    @staticmethod
    def _valid_price(price: Decimal | str) -> Decimal:
        value = price if isinstance(price, Decimal) else Decimal(str(price))
        if not value.is_finite() or value <= 0:
            raise ValueError("price must be positive and finite")
        return value

    def _match(self, side: str, quantity: int, limit: Decimal | None = None):
        self._valid_quantity(quantity)
        remaining = quantity
        trades: list[tuple[Decimal, int]] = []
        opposite = "sell" if side == "buy" else "buy"
        while remaining:
            candidates = self.ordered(opposite)
            if not candidates:
                break
            maker = candidates[0]
            if limit is not None:
                crossed = maker.price <= limit if side == "buy" else maker.price >= limit
                if not crossed:
                    break
            fill = min(remaining, maker.quantity)
            remaining -= fill
            maker.quantity -= fill
            if trades and trades[-1][0] == maker.price:
                prior_price, prior_quantity = trades[-1]
                trades[-1] = (prior_price, prior_quantity + fill)
            else:
                trades.append((maker.price, fill))
            if maker.quantity == 0:
                self.orders.remove(maker)
                if not maker.peg:
                    self._sync(maker.side)
        return remaining, tuple(trades)

    def limit(self, side: str, price: Decimal | str, quantity: int):
        value = self._valid_price(price)
        self._valid_quantity(quantity)
        order_id = self._new_id()
        remaining, trades = self._match(side, quantity, value)
        if remaining:
            self._rest(RefOrder(order_id, side, value, remaining))
            self._sync(side)
        return order_id, trades

    def market(self, side: str, quantity: int):
        return self._match(side, quantity)[1]

    def peg_order(self, side: str, reference: str, quantity: int) -> str:
        self._valid_quantity(quantity)
        regular = self.best_regular(side)
        if regular is None:
            raise ReferenceMissingReferenceError("no regular reference")
        order_id = self._new_id()
        self._rest(RefOrder(order_id, side, regular.price, quantity, True, reference))
        return order_id

    def cancel(self, order_id: str) -> None:
        for order in self.orders:
            if order.order_id == order_id:
                self.orders.remove(order)
                if not order.peg:
                    self._sync(order.side)
                return
        raise KeyError(order_id)

    def amend(self, order_id: str, price=None, quantity=None):
        order = next((item for item in self.orders if item.order_id == order_id), None)
        if order is None:
            raise KeyError(order_id)
        if price is None and quantity is None:
            raise ValueError("empty amendment")
        if quantity is not None:
            self._valid_quantity(quantity)
        if order.peg:
            if price is not None:
                raise ValueError("peg price cannot be amended")
            if quantity is None:
                raise ValueError("peg quantity is required")
            if quantity < order.quantity:
                order.quantity = quantity
            elif quantity > order.quantity:
                self.orders.remove(order)
                order.quantity = quantity
                self._rest(order)
            return order_id, ()
        new_price = order.price if price is None else self._valid_price(price)
        new_quantity = order.quantity if quantity is None else quantity
        price_changed = new_price != order.price
        quantity_increased = new_quantity > order.quantity
        if not price_changed and not quantity_increased:
            order.quantity = new_quantity
            return order_id, ()
        self.orders.remove(order)
        self._sync(order.side)
        remaining, trades = self._match(order.side, new_quantity, new_price)
        if remaining:
            order.price = new_price
            order.quantity = remaining
            self._rest(order)
            self._sync(order.side)
        return order_id, trades

    def snapshot(self):
        values = self.ordered("buy") + self.ordered("sell")
        return tuple((order.order_id, order.side, order.price, order.quantity,
                      order.peg, order.reference) for order in values)
