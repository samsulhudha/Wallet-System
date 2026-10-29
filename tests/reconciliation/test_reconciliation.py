import pytest
from apps.wallets.models import Wallet
from apps.payments.services import PaymentService
from apps.ledger.reconciliation import ReconciliationService


@pytest.mark.django_db(transaction=True)
def test_reconciliation_detects_wallet_balance_tampering(wallet_a_inr, user_a):
    # Perform legitimate topup
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)

    # Initial reconciliation should be healthy
    report = ReconciliationService.reconcile_wallet(wallet_a_inr)
    assert report.is_balanced is True
    assert report.available_discrepancy == 0

    # Tamper with cached balance in database without posting ledger entry
    wallet_a_inr.available_balance += 9999
    wallet_a_inr.save(update_fields=['available_balance'])

    # Reconciliation MUST detect discrepancy
    tampered_report = ReconciliationService.reconcile_wallet(wallet_a_inr)
    assert tampered_report.is_balanced is False
    assert tampered_report.available_discrepancy == 9999
    assert tampered_report.expected_available == 50000
    assert tampered_report.cached_available == 59999


@pytest.mark.django_db(transaction=True)
def test_reconciliation_across_transfers_and_reversals(wallet_a_inr, wallet_b_inr, user_a, admin_user):
    PaymentService.topup(wallet_a_inr.id, 100000, "INR", user_a)
    transfer = PaymentService.transfer(wallet_a_inr.id, wallet_b_inr.id, 40000, "INR", user_a)

    # Check after transfer
    report_trans = ReconciliationService.run_full_reconciliation()
    assert report_trans.is_healthy is True

    # Reverse transfer
    PaymentService.reverse_transfer(transfer.id, admin_user, "Test audit reversal")

    # Check after reversal
    report_rev = ReconciliationService.run_full_reconciliation()
    assert report_rev.is_healthy is True
    assert report_rev.discrepant_wallets_count == 0
    assert report_rev.unbalanced_transactions_count == 0


@pytest.mark.django_db(transaction=True)
def test_reconciliation_on_failed_withdrawal_refund(wallet_a_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    withdrawal = PaymentService.request_withdrawal(wallet_a_inr.id, 20000, "INR", user_a)

    # Check while reserved
    rep_reserved = ReconciliationService.reconcile_wallet(wallet_a_inr)
    assert rep_reserved.is_balanced is True
    assert rep_reserved.cached_reserved == 20000
    assert rep_reserved.cached_available == 30000

    # Mock bank failure callback
    callback_payload = {
        "provider_event_id": "evt_fail_recon_test",
        "provider_reference": withdrawal.provider_reference,
        "event_type": "PAYOUT_REJECTED",
        "failure_reason": "Account invalid"
    }
    from apps.payments.mock_bank import MockBankService
    sig = MockBankService.generate_signature(callback_payload)
    PaymentService.process_bank_callback(callback_payload, sig)

    # Check after refund
    wallet_a_inr.refresh_from_db()
    rep_refunded = ReconciliationService.reconcile_wallet(wallet_a_inr)
    assert rep_refunded.is_balanced is True
    assert rep_refunded.cached_reserved == 0
    assert rep_refunded.cached_available == 50000
    assert rep_refunded.expected_available == 50000
