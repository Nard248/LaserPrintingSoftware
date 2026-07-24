"""Error hierarchy for the labgate platform.

Every error the API surfaces maps onto one of these; the FastAPI layer
translates them to HTTP status codes (AuthError -> 401/403,
TransitionError -> 409, ValidationFailed -> 422, DeviceError -> 502).
"""


class LabgateError(Exception):
    """Base class for all platform errors."""


class ValidationFailed(LabgateError):
    """A spec failed deterministic validation."""


class TransitionError(LabgateError):
    """An operation was attempted in an illegal plan state."""


class AuthError(LabgateError):
    """Authentication or authorization failure."""


class DeviceError(LabgateError):
    """A device adapter could not perform the requested action."""
