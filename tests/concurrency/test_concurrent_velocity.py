import pytest
import concurrent.futures
from django.db import connections
from apps.wallets.models import Wallet
from apps.payments.services import PaymentService
from apps.risk.models import RiskRuleConfig, RiskAction
from apps.common.exceptions import FraudDetectedError


@pytest.mark.django_db(transaction=True)
def test_parallel_requests_cannot_bypass_velocity_threshold(wallet_a_inr, wallet_b_inr, user_a):
    """
    Scenario:
    Velocity threshold configured: max 5 transfers within 5 minutes.
    15 parallel threads simultaneously attempt to execute a transfer.
    Under row-level locking on wallet A, requests are serialized:
    Exactly 5 transfers succeed, and the remaining 10 are blocked by FraudDetectedError.
    """
    # Configure rule in DB: limit 5 transfers
    RiskRuleConfig.objects.update_or_create(
        rule_id='rule_velocity_transfers',
        defaults={
            'is_enabled': True,
            'parameters': {'max_transfers': 5, 'window_minutes': 5, 'action': RiskAction.BLOCK}
        }
    )

    # Fund wallet with plenty of balance
    PaymentService.topup(wallet_a_inr.id, 500000, "INR", user_a)

    success_count = 0
    blocked_count = 0

    def attempt_transfer(idx):
        connections.close_all()
        try:
            PaymentService.transfer(
                source_wallet_id=wallet_a_inr.id,
                dest_wallet_id=wallet_b_inr.id,
                amount=1000,
                currency="INR",
                user=user_a
            )
            return "SUCCESS", None
        except FraudDetectedError:
            return "BLOCKED", None
        except Exception as e:
            return "ERROR", str(e)
        finally:
            connections.close_all()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(attempt_transfer, i) for i in range(15)]
        for f in concurrent.futures.as_completed(futures):
            status, err = f.result()
            if status == "SUCCESS":
                success_count += 1
            elif status == "BLOCKED":
                blocked_count += 1

    assert success_count == 5, f"Expected exactly 5 allowed transfers, got {success_count}"
    assert blocked_count == 10, f"Expected 10 blocked transfers, got {blocked_count}"
