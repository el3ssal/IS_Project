"""Custom exception hierarchy (Phase 1, SRS §17.2).

UI handlers catch these and show a user-friendly message (gr.Info/gr.Error).
Unexpected exceptions are logged, never shown with a traceback.
"""
from __future__ import annotations


class ApplicationError(Exception):
    """Base class for all expected, handled application errors."""


class ValidationError(ApplicationError):
    """Input or business-rule validation failed (bad input, bad state)."""


class AuthenticationError(ApplicationError):
    """Login failed, session invalid/expired, or account locked/disabled."""


class AuthorizationError(ApplicationError):
    """The logged-in user lacks the permission for this operation."""


class NotFoundError(ApplicationError):
    """A requested record does not exist (or the user may not see it)."""


class BookingConflictError(ApplicationError):
    """The requested hall/time conflicts with an existing booking or block."""


class HallUnavailableError(ApplicationError):
    """The hall cannot be booked (inactive, closed, maintenance, hours)."""


class InsufficientDepositError(ApplicationError):
    """The recorded deposit does not meet the required deposit."""


class PaymentError(ApplicationError):
    """A payment operation failed validation or state rules."""


class RefundLimitExceededError(ApplicationError):
    """A refund would exceed the refundable amount for its payment/booking."""


class DatabaseOperationError(ApplicationError):
    """A database operation failed (including busy/locked after retries)."""


class AttachmentValidationError(ApplicationError):
    """An uploaded file failed type/size/content validation."""
