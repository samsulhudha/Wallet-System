import logging
from typing import Tuple, Optional, Any, Dict
from django.db import transaction, IntegrityError
from apps.accounts.models import User
from .models import IdempotencyRecord, IdempotencyStatus
from apps.common.exceptions import IdempotencyConflictError, IdempotencyKeyRequiredError

logger = logging.getLogger(__name__)


class IdempotencyManager:
    """
    Coordinates atomic idempotency tracking.
    Guarantees that retry requests with the same key and payload receive identical responses,
    while payload mutations trigger HTTP 409 Conflict.
    """

    @classmethod
    def get_idempotency_key(cls, request) -> str:
        key = request.headers.get('Idempotency-Key') or request.META.get('HTTP_IDEMPOTENCY_KEY')
        if not key or not key.strip():
            raise IdempotencyKeyRequiredError("Missing required 'Idempotency-Key' HTTP header.")
        return key.strip()

    @classmethod
    def acquire(
        cls,
        user: User,
        key: str,
        endpoint: str,
        payload: dict
    ) -> Tuple[IdempotencyRecord, bool]:
        """
        Attempts to acquire an idempotency lock for (user, key, endpoint).
        Returns:
            (record, is_new: bool)
            - If is_new is True: caller proceeds with business operation.
            - If is_new is False: caller returns existing cached response.
        """
        request_hash = IdempotencyRecord.compute_request_hash(payload)

        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(
                    user=user,
                    idempotency_key=key,
                    endpoint=endpoint,
                    request_hash=request_hash,
                    status=IdempotencyStatus.PENDING,
                )
                return record, True
        except IntegrityError:
            # Another request with this key already exists
            record = IdempotencyRecord.objects.filter(
                user=user,
                idempotency_key=key,
                endpoint=endpoint
            ).first()

            if not record:
                # Race condition edge case: fetch failed
                raise IdempotencyConflictError("Concurrent idempotency conflict. Please retry.")

            # Validate payload integrity
            if record.request_hash != request_hash:
                raise IdempotencyConflictError(
                    f"Idempotency key '{key}' was already used with a different request payload."
                )

            if record.status == IdempotencyStatus.PENDING:
                raise IdempotencyConflictError(
                    f"A request with idempotency key '{key}' is currently being processed. Please retry."
                )

            if record.status == IdempotencyStatus.RESOLVED:
                logger.info(
                    "Returning cached response for idempotency key %s (user %s)",
                    key, user.username
                )
                return record, False

            # If previous failed, allow retry
            with transaction.atomic():
                record.status = IdempotencyStatus.PENDING
                record.request_hash = request_hash
                record.save(update_fields=['status', 'request_hash', 'updated_at'])
                return record, True

    @classmethod
    def resolve(
        cls,
        record: IdempotencyRecord,
        status_code: int,
        response_data: Any,
        resource_id: Optional[str] = None
    ) -> None:
        """Atomically marks the idempotency record as RESOLVED with response payload."""
        record.status = IdempotencyStatus.RESOLVED
        record.response_code = status_code
        record.response_data = response_data
        if resource_id:
            record.resource_id = str(resource_id)
        record.save(update_fields=['status', 'response_code', 'response_data', 'resource_id', 'updated_at'])

    @classmethod
    def fail(cls, record: IdempotencyRecord, error_data: Optional[Dict] = None) -> None:
        """Marks the idempotency record as FAILED on unexpected operational failure."""
        record.status = IdempotencyStatus.FAILED
        if error_data:
            record.response_data = error_data
        record.save(update_fields=['status', 'response_data', 'updated_at'])

