"""Public domain API for the matching engine."""

from .engine import MatchingEngine
from .exceptions import (
    InvalidOrderError,
    MatchingEngineError,
    MissingReferenceError,
    UnknownOrderError,
    ValidationError,
)
from .models import (
    BookEntry,
    BookSnapshot,
    OperationResult,
    OrderType,
    PegReference,
    Side,
    Trade,
)
from .order_book import LimitOrderBook, RestingOrder

__all__ = [
    "BookEntry",
    "BookSnapshot",
    "InvalidOrderError",
    "LimitOrderBook",
    "MatchingEngine",
    "MatchingEngineError",
    "MissingReferenceError",
    "OperationResult",
    "OrderType",
    "PegReference",
    "RestingOrder",
    "Side",
    "Trade",
    "UnknownOrderError",
    "ValidationError",
]
