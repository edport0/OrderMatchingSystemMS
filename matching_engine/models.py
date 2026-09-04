"""Small, dependency-free domain types used by the matching engine.

The matching engine itself is deliberately not part of this first scaffold.
These types establish the public vocabulary shared by the order book, engine,
and command-line adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from .exceptions import ValidationError


class Side(str, Enum):
    """The direction of an order."""

    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> "Side":
        """Return the side an order can trade against."""

        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(str, Enum):
    """Supported order kinds."""

    LIMIT = "limit"
    MARKET = "market"
    PEGGED = "pegged"


class PegReference(str, Enum):
    """Book price followed by a pegged order."""

    BID = "bid"
    OFFER = "offer"


def _decimal_price(value: Any) -> Decimal:
    """Convert a price to an exact positive finite Decimal."""

    if isinstance(value, bool):
        raise ValidationError("price must be a positive finite decimal")
    try:
        price = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("price must be a positive finite decimal") from None
    if not price.is_finite() or price <= 0:
        raise ValidationError("price must be a positive finite decimal")
    return price


def _quantity(value: Any) -> int:
    """Validate a positive integer quantity without accepting bool as an int."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError("quantity must be a positive integer")
    return value


def _side(value: Side | str) -> Side:
    try:
        return value if isinstance(value, Side) else Side(value)
    except (TypeError, ValueError):
        raise ValidationError("side must be 'buy' or 'sell'") from None


def _order_type(value: OrderType | str) -> OrderType:
    try:
        return value if isinstance(value, OrderType) else OrderType(value)
    except (TypeError, ValueError):
        raise ValidationError("order type must be limit, market, or pegged") from None


def _peg_reference(value: PegReference | str | None) -> PegReference | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, PegReference) else PegReference(value)
    except (TypeError, ValueError):
        raise ValidationError("peg reference must be 'bid' or 'offer'") from None


@dataclass(frozen=True, slots=True)
class Trade:
    """A single execution report, at one price and quantity."""

    price: Decimal
    qty: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _decimal_price(self.price))
        object.__setattr__(self, "qty", _quantity(self.qty))

    @property
    def quantity(self) -> int:
        """Readable synonym used by the order-book API."""

        return self.qty


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Result of an order operation.

    ``order_id`` identifies a placed or amended resting order, including one
    that was immediately filled. Market orders are transient and have no ID.
    """

    order_id: str | None = None
    trades: tuple[Trade, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.order_id is not None and (
            not isinstance(self.order_id, str) or not self.order_id.strip()
        ):
            raise ValidationError("order_id must be a non-empty string")
        if not isinstance(self.trades, tuple):
            object.__setattr__(self, "trades", tuple(self.trades))
        if any(not isinstance(trade, Trade) for trade in self.trades):
            raise ValidationError("trades must contain only Trade values")

    @property
    def created_order_id(self) -> str | None:
        """Alias clarifying the ID's meaning for placement operations."""

        return self.order_id


@dataclass(frozen=True, slots=True)
class BookEntry:
    """One visible resting order in a book snapshot."""

    order_id: str
    side: Side
    price: Decimal
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    peg_reference: PegReference | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValidationError("order_id must be a non-empty string")
        object.__setattr__(self, "side", _side(self.side))
        object.__setattr__(self, "price", _decimal_price(self.price))
        object.__setattr__(self, "quantity", _quantity(self.quantity))
        object.__setattr__(self, "order_type", _order_type(self.order_type))
        reference = _peg_reference(self.peg_reference)
        if self.order_type is OrderType.PEGGED and reference is None:
            raise ValidationError("pegged entries require a peg reference")
        if self.order_type is not OrderType.PEGGED and reference is not None:
            raise ValidationError("only pegged entries may have a peg reference")
        object.__setattr__(self, "peg_reference", reference)

    @property
    def qty(self) -> int:
        """Short synonym for quantity, matching command terminology."""

        return self.quantity


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """Immutable view of the currently visible bid and offer orders."""

    bids: tuple[BookEntry, ...] = field(default_factory=tuple)
    offers: tuple[BookEntry, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.bids, tuple):
            object.__setattr__(self, "bids", tuple(self.bids))
        if not isinstance(self.offers, tuple):
            object.__setattr__(self, "offers", tuple(self.offers))
        if any(not isinstance(entry, BookEntry) for entry in (*self.bids, *self.offers)):
            raise ValidationError("book sides must contain only BookEntry values")
        if any(entry.side is not Side.BUY for entry in self.bids):
            raise ValidationError("bids must contain buy entries")
        if any(entry.side is not Side.SELL for entry in self.offers):
            raise ValidationError("offers must contain sell entries")


__all__ = [
    "BookEntry",
    "BookSnapshot",
    "OperationResult",
    "OrderType",
    "PegReference",
    "Side",
    "Trade",
]
