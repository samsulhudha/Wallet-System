import threading
import uuid
import pytest
from django.db import connection
from apps.accounts.models import User
from apps.wallets.models import Wallet
from apps.payments.services import PaymentService
from apps.ledger.reconciliation import ReconciliationService


@pytest.mark.django_db(transaction=True)
def test_concurrent_topups_aggregate_balance_integrity():
    """
    Spawns 20 concurrent threads simultaneously topping up the same wallet with ₹100.00 (10,000 paise).
    PostgreSQL row-level locking on the wallet must serialize balance updates.
    Final balance must be exactly ₹2,000.00 (200,000 paise), and ledger entries must reconcile to 0 discrepancy.
    """
    user = User.objects.create_user(username="conc_topup_user", email="topup_conc@wallet.local", password="Password123!")
    wallet = Wallet.objects.create(user=user, currency="INR", available_balance=0, reserved_balance=0)

    num_threads = 20
    topup_amount_paise = 10000  # ₹100.00
    expected_total_paise = num_threads * topup_amount_paise

    results = []
    errors = []

    def run_topup():
        connection.close()
        try:
            topup_obj = PaymentService.topup(
                wallet_id=wallet.id,
                amount=topup_amount_paise,
                currency="INR",
                user=user,
                provider_outcome="SUCCESS",
                description="Concurrent top-up stress test"
            )
            results.append(topup_obj.id)
        except Exception as e:
            errors.append(str(e))
        finally:
            connection.close()

    threads = [threading.Thread(target=run_topup) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Encountered errors during concurrent top-ups: {errors}"
    assert len(results) == num_threads

    wallet.refresh_from_db()
    assert wallet.available_balance == expected_total_paise
    assert wallet.reserved_balance == 0

    # Reconciliation audit
    report = ReconciliationService.reconcile_wallet(wallet)
    assert report.is_balanced is True
    assert report.expected_available == expected_total_paise
    assert report.available_discrepancy == 0

