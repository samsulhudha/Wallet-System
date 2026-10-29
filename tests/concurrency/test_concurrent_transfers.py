import pytest
import concurrent.futures
from django.db import connections
from apps.wallets.models import Wallet
from apps.payments.services import PaymentService
from apps.ledger.reconciliation import ReconciliationService
from apps.risk.models import RiskRuleConfig
from apps.common.exceptions import InsufficientFundsError


@pytest.mark.django_db(transaction=True)
def test_100_simultaneous_transfers_insufficient_aggregate_funds(wallet_a_inr, wallet_b_inr, user_a):
    """
    Scenario:
    Wallet A has 100,000 minor units (1,000.00 INR).
    Wallet B has 0.
    100 concurrent threads attempt to transfer 5,000 minor units each from A to B.
    Total requested: 500,000 minor units.
    Available funds: 100,000 minor units.

    Invariant:
    Exactly 20 transfers must succeed.
    Exactly 80 transfers must fail with InsufficientFundsError.
    Wallet A ending balance MUST be exactly 0 (never negative).
    Wallet B ending balance MUST be exactly 100,000.
    """
    initial_balance = 100000
    transfer_amount = 5000
    expected_success_count = initial_balance // transfer_amount  # 20
    total_attempts = 100

    # Disable velocity check for this balance exhaustion test
    RiskRuleConfig.objects.update_or_create(
        rule_id='rule_velocity_transfers',
        defaults={'is_enabled': False}
    )

    PaymentService.topup(wallet_a_inr.id, initial_balance, "INR", user_a)

    success_count = 0
    failure_count = 0

    def attempt_transfer(idx):
        connections.close_all()
        try:
            PaymentService.transfer(
                source_wallet_id=wallet_a_inr.id,
                dest_wallet_id=wallet_b_inr.id,
                amount=transfer_amount,
                currency="INR",
                user=user_a,
                description=f"Concurrent transfer #{idx}"
            )
            return True, None
        except InsufficientFundsError:
            return False, "INSUFFICIENT_FUNDS"
        except Exception as e:
            return False, str(e)
        finally:
            connections.close_all()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(attempt_transfer, i) for i in range(total_attempts)]
        for f in concurrent.futures.as_completed(futures):
            ok, err = f.result()
            if ok:
                success_count += 1
            else:
                failure_count += 1

    assert success_count == expected_success_count, f"Expected {expected_success_count} successes, got {success_count}"
    assert failure_count == (total_attempts - expected_success_count)

    wallet_a_inr.refresh_from_db()
    wallet_b_inr.refresh_from_db()

    assert wallet_a_inr.available_balance == 0
    assert wallet_b_inr.available_balance == initial_balance

    # Audit reconciliation
    report = ReconciliationService.run_full_reconciliation()
    assert report.is_healthy is True
