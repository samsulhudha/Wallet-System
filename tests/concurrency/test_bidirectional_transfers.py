import pytest
import concurrent.futures
from django.db import connections
from apps.wallets.models import Wallet
from apps.payments.services import PaymentService
from apps.ledger.reconciliation import ReconciliationService
from apps.risk.models import RiskRuleConfig


@pytest.mark.django_db(transaction=True)
def test_bidirectional_transfers_no_deadlock(wallet_a_inr, wallet_b_inr, user_a, user_b):
    """
    Scenario:
    Wallet A has 50,000 minor units.
    Wallet B has 50,000 minor units.
    20 threads run concurrent transfers:
    - 10 threads transfer 1,000 from A -> B
    - 10 threads transfer 1,000 from B -> A
    Guarantees:
    - Strict ascending PK locking prevents all database deadlocks.
    - All transfers complete successfully.
    - Total money across both wallets remains exactly 100,000.
    """
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    PaymentService.topup(wallet_b_inr.id, 50000, "INR", user_b)

    # Disable velocity check for bidirectional deadlock test
    RiskRuleConfig.objects.update_or_create(
        rule_id='rule_velocity_transfers',
        defaults={'is_enabled': False}
    )

    transfer_amount = 1000
    ops_per_direction = 10

    def transfer_a_to_b(idx):
        connections.close_all()
        try:
            PaymentService.transfer(
                source_wallet_id=wallet_a_inr.id,
                dest_wallet_id=wallet_b_inr.id,
                amount=transfer_amount,
                currency="INR",
                user=user_a
            )
            return True, None
        except Exception as e:
            return False, str(e)
        finally:
            connections.close_all()

    def transfer_b_to_a(idx):
        connections.close_all()
        try:
            PaymentService.transfer(
                source_wallet_id=wallet_b_inr.id,
                dest_wallet_id=wallet_a_inr.id,
                amount=transfer_amount,
                currency="INR",
                user=user_b
            )
            return True, None
        except Exception as e:
            return False, str(e)
        finally:
            connections.close_all()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures_a = [executor.submit(transfer_a_to_b, i) for i in range(ops_per_direction)]
        futures_b = [executor.submit(transfer_b_to_a, i) for i in range(ops_per_direction)]

        all_futures = futures_a + futures_b
        results = [f.result() for f in concurrent.futures.as_completed(all_futures)]

    # All should succeed without deadlock
    assert all(ok for ok, _ in results), f"Some transfers failed: {[err for ok, err in results if not ok]}"

    wallet_a_inr.refresh_from_db()
    wallet_b_inr.refresh_from_db()

    assert wallet_a_inr.available_balance + wallet_b_inr.available_balance == 100000

    report = ReconciliationService.run_full_reconciliation()
    assert report.is_healthy is True
