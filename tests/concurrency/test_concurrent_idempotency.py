import pytest
import concurrent.futures
from django.db import connections
from apps.wallets.models import Wallet
from apps.payments.models import Transfer, IdempotencyRecord
from apps.payments.services import PaymentService
from apps.payments.idempotency import IdempotencyManager


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_idempotency_keys(wallet_a_inr, wallet_b_inr, user_a):
    """
    Scenario:
    10 threads concurrently invoke an operation with the EXACT SAME idempotency key.
    Guarantees:
    - Only 1 thread executes the transfer.
    - Money is deducted exactly once.
    - Only 1 Transfer database record is created.
    """
    PaymentService.topup(wallet_a_inr.id, 100000, "INR", user_a)
    shared_key = "concurrent-key-999"
    endpoint = "POST /api/transfers/"
    payload = {
        "source_wallet_id": wallet_a_inr.id,
        "destination_wallet_id": wallet_b_inr.id,
        "amount": 10000,
        "currency": "INR"
    }

    def execute_idempotent_operation(idx):
        connections.close_all()
        try:
            record, is_new = IdempotencyManager.acquire(
                user=user_a,
                key=shared_key,
                endpoint=endpoint,
                payload=payload
            )
            if is_new:
                # Execute business logic
                transfer = PaymentService.transfer(
                    source_wallet_id=wallet_a_inr.id,
                    dest_wallet_id=wallet_b_inr.id,
                    amount=10000,
                    currency="INR",
                    user=user_a
                )
                IdempotencyManager.resolve(record, 201, {"id": transfer.id}, resource_id=str(transfer.id))
                return "EXECUTED"
            else:
                return "CACHED"
        except Exception as e:
            return f"CONFLICT: {type(e).__name__}"
        finally:
            connections.close_all()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(execute_idempotent_operation, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Exactly 1 thread must have executed
    executed_count = sum(1 for r in results if r == "EXECUTED")
    assert executed_count == 1, f"Expected exactly 1 execution, got {executed_count}. Results: {results}"

    # Verify database state: exactly 1 transfer created
    assert Transfer.objects.filter(source_wallet=wallet_a_inr).count() == 1

    wallet_a_inr.refresh_from_db()
    wallet_b_inr.refresh_from_db()

    assert wallet_a_inr.available_balance == 90000
    assert wallet_b_inr.available_balance == 10000

