import pytest
import concurrent.futures
from django.db import connections
from apps.wallets.models import Wallet
from apps.payments.models import Withdrawal, WithdrawalStatus, BankCallbackLog
from apps.payments.mock_bank import MockBankService
from apps.payments.services import PaymentService
from apps.ledger.models import LedgerEntry


@pytest.mark.django_db(transaction=True)
def test_concurrent_bank_callbacks(wallet_a_inr, user_a):
    """
    Scenario:
    10 threads concurrently deliver bank callbacks for the same withdrawal.
    Guarantees:
    - Only 1 callback completes settlement.
    - Reserved funds are decremented exactly once.
    - Exactly 1 settlement ledger transaction is posted.
    """
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    withdrawal = PaymentService.request_withdrawal(wallet_a_inr.id, 20000, "INR", user_a)

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.reserved_balance == 20000
    assert wallet_a_inr.available_balance == 30000

    def deliver_callback(idx):
        connections.close_all()
        try:
            # Each thread delivers with a distinct event_id or duplicate event_id
            payload = {
                "provider_event_id": f"event_concurrent_{idx}",
                "provider_reference": withdrawal.provider_reference,
                "event_type": "PAYOUT_COMPLETED",
                "amount": 20000,
                "currency": "INR"
            }
            sig = MockBankService.generate_signature(payload)
            success, msg = PaymentService.process_bank_callback(payload, sig)
            return success, msg
        finally:
            connections.close_all()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(deliver_callback, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # All should return acknowledged or finalized without error
    assert all(ok for ok, _ in results)

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.reserved_balance == 0
    assert wallet_a_inr.available_balance == 30000

    withdrawal.refresh_from_db()
    assert withdrawal.status == WithdrawalStatus.COMPLETED

    # Settlement entries count MUST be exactly 1
    settle_entries = LedgerEntry.objects.filter(
        account__wallet=wallet_a_inr,
        transaction__transaction_type='WITHDRAWAL_SETTLE'
    )
    assert settle_entries.count() == 1
