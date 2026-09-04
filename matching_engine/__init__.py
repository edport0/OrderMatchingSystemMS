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
__all__ = [
    "BookEntry",
    "BookSnapshot",
    "InvalidOrderError",
    "MatchingEngine",
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
