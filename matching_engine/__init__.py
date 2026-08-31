"""Public domain API for the matching engine."""

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

__all__ = [
    "BookEntry",
    "BookSnapshot",
    "InvalidOrderError",
    "MatchingEngineError",
    "MissingReferenceError",
    "OperationResult",
    "OrderType",
    "PegReference",
    "Side",
    "Trade",
    "UnknownOrderError",
    "ValidationError",
]
