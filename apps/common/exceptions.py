import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError, AuthenticationFailed, NotAuthenticated, PermissionDenied

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Base class for all domain errors."""
    default_code = "DOMAIN_ERROR"
    default_status = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, code: str = None, status_code: int = None, details: dict = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.status_code = status_code or self.default_status
        self.details = details or {}


class InsufficientFundsError(DomainError):
    default_code = "INSUFFICIENT_FUNDS"
    default_status = status.HTTP_400_BAD_REQUEST


class WalletStateError(DomainError):
    default_code = "INVALID_WALLET_STATE"
    default_status = status.HTTP_400_BAD_REQUEST


class SelfTransferError(DomainError):
    default_code = "SELF_TRANSFER_DISALLOWED"
    default_status = status.HTTP_400_BAD_REQUEST


class CurrencyMismatchError(DomainError):
    default_code = "CURRENCY_MISMATCH"
    default_status = status.HTTP_400_BAD_REQUEST


class DailyLimitExceededError(DomainError):
    default_code = "DAILY_LIMIT_EXCEEDED"
    default_status = status.HTTP_400_BAD_REQUEST


class FraudDetectedError(DomainError):
    default_code = "FRAUD_DETECTED"
    default_status = status.HTTP_403_FORBIDDEN


class IdempotencyConflictError(DomainError):
    default_code = "IDEMPOTENCY_CONFLICT"
    default_status = status.HTTP_409_CONFLICT


class IdempotencyKeyRequiredError(DomainError):
    default_code = "IDEMPOTENCY_KEY_REQUIRED"
    default_status = status.HTTP_400_BAD_REQUEST


class InvalidStateTransitionError(DomainError):
    default_code = "INVALID_STATE_TRANSITION"
    default_status = status.HTTP_400_BAD_REQUEST


class ReversalNotEligibleError(DomainError):
    default_code = "REVERSAL_NOT_ELIGIBLE"
    default_status = status.HTTP_400_BAD_REQUEST


def custom_exception_handler(exc, context):
    """
    Standardized DRF Exception Handler.
    Guarantees every error response has shape:
    {
        "error": {
            "code": "CODE",
            "message": "Human readable message",
            "details": {...}
        }
    }
    """
    # Domain exceptions
    if isinstance(exc, DomainError):
        logger.warning("DomainError encountered: %s (code=%s)", exc.message, exc.code)
        return Response(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
            status=exc.status_code,
        )

    # Let standard DRF handler process first
    response = exception_handler(exc, context)

    if response is not None:
        code = "API_ERROR"
        message = "An error occurred."
        details = {}

        if isinstance(exc, ValidationError):
            code = "VALIDATION_ERROR"
            message = "Validation failed for one or more fields."
            details = response.data
        elif isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            code = "AUTHENTICATION_FAILED"
            message = str(response.data.get("detail", "Authentication credentials were not provided or are invalid."))
        elif isinstance(exc, PermissionDenied):
            code = "PERMISSION_DENIED"
            message = str(response.data.get("detail", "You do not have permission to perform this action."))
        elif hasattr(response, "data") and isinstance(response.data, dict) and "detail" in response.data:
            message = str(response.data["detail"])
            code = getattr(exc, "default_code", "API_ERROR").upper()
        else:
            details = response.data

        response.data = {
            "error": {
                "code": code,
                "message": message,
                "details": details,
            }
        }
        return response

    # Unhandled server errors (500)
    logger.exception("Unhandled server exception occurred: %s", exc)
    return Response(
        {
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected internal server error occurred.",
                "details": {},
            }
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )

