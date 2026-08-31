"""Exceptions raised by the matching engine domain and public API."""


class MatchingEngineError(Exception):
    """Base class for expected matching-engine errors."""


class ValidationError(MatchingEngineError, ValueError):
    """Raised when an order or value does not satisfy domain constraints."""


class UnknownOrderError(MatchingEngineError, LookupError):
    """Raised when an operation refers to an order that is not known."""


class MissingReferenceError(ValidationError):
    """Raised when a pegged order has no price to use as its initial reference."""


# A descriptive alias for callers that prefer the more explicit name.
InvalidOrderError = ValidationError
