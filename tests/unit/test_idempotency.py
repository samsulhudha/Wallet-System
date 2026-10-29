import pytest
from apps.payments.models import IdempotencyRecord, IdempotencyStatus
from apps.payments.idempotency import IdempotencyManager
from apps.common.exceptions import IdempotencyConflictError


def test_request_hash_determinism():
    payload1 = {"b": 2, "a": 1, "nested": {"y": "val", "x": 10}}
    payload2 = {"a": 1, "b": 2, "nested": {"x": 10, "y": "val"}}

    hash1 = IdempotencyRecord.compute_request_hash(payload1)
    hash2 = IdempotencyRecord.compute_request_hash(payload2)

    assert hash1 == hash2

    altered_payload = {"a": 1, "b": 3, "nested": {"x": 10, "y": "val"}}
    hash3 = IdempotencyRecord.compute_request_hash(altered_payload)
    assert hash1 != hash3


@pytest.mark.django_db
def test_idempotency_lifecycle(user_a):
    key = "idem-test-key-1"
    endpoint = "POST /api/transfers/"
    payload = {"source_wallet_id": 1, "destination_wallet_id": 2, "amount": "100.00"}

    # 1. Acquire new
    record, is_new = IdempotencyManager.acquire(user=user_a, key=key, endpoint=endpoint, payload=payload)
    assert is_new is True
    assert record.status == IdempotencyStatus.PENDING

    # 2. Resolve
    response_data = {"id": 10, "status": "COMPLETED"}
    IdempotencyManager.resolve(record, 201, response_data, resource_id="10")

    # 3. Identical retry returns cached response
    record_retry, is_new_retry = IdempotencyManager.acquire(user=user_a, key=key, endpoint=endpoint, payload=payload)
    assert is_new_retry is False
    assert record_retry.response_code == 201
    assert record_retry.response_data == response_data

    # 4. Same key with different payload raises 409 conflict
    different_payload = {"source_wallet_id": 1, "destination_wallet_id": 2, "amount": "200.00"}
    with pytest.raises(IdempotencyConflictError, match="different request payload"):
        IdempotencyManager.acquire(user=user_a, key=key, endpoint=endpoint, payload=different_payload)
