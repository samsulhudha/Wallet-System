import pytest
import uuid
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from apps.accounts.models import User
from apps.wallets.models import Wallet, WalletState
from apps.payments.models import Withdrawal, WithdrawalStatus, BankCallbackLog
from apps.payments.mock_bank import MockBankService
from apps.payments.services import PaymentService
from apps.ledger.models import LedgerEntry, LedgerTransaction, TransactionType, EntryDirection
from apps.ledger.reconciliation import ReconciliationService


@pytest.mark.django_db
class TestCriticalWithdrawalAndRefundLifecycle:
    """
    Validates Phase 5 requirements:
    - Exact 10-step critical refund scenario:
      1. Create wallet
      2. Top-up 10,000
      3. Request withdrawal of 3,000
      4. Verify 3,000 reserved
      5. Verify available balance is 7,000
      6. Trigger bank failure callback
      7. Verify refund through compensating ledger entries
      8. Verify available returns to 10,000
      9. Send same failure callback again
      10. Verify no second refund occurs
    - Duplicate success callbacks
    - Conflicting success and failure callbacks
    - Closed / Frozen wallet withdrawal rejection
    """

    def test_ten_step_critical_refund_scenario(self):
        # 1. Create a user & wallet
        user = User.objects.create_user(username="refund_user", email="refund@wallet.local", password="Password123!")
        client = APIClient()
        token = str(RefreshToken.for_user(user).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        resp = client.post("/api/wallets/", {"currency": "INR"}, format='json')
        assert resp.status_code == 201
        wallet_id = resp.data['id']

        # 2. Top-up 10,000 INR (1,000,000 paise)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_IDEMPOTENCY_KEY="refund_topup_10000")
        resp = client.post(f"/api/wallets/{wallet_id}/topups/", {
            "amount": "10000.00",
            "currency": "INR"
        }, format='json')
        assert resp.status_code == 201

        wallet = Wallet.objects.get(id=wallet_id)
        assert wallet.available_balance == 1000000
        assert wallet.reserved_balance == 0

        # 3. Request withdrawal of 3,000 INR (300,000 paise)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_IDEMPOTENCY_KEY="refund_wdr_3000")
        resp = client.post(f"/api/wallets/{wallet_id}/withdrawals/", {
            "amount": "3000.00",
            "currency": "INR",
            "destination_bank_account": "HDFC_REFUND_001"
        }, format='json')
        assert resp.status_code == 202
        wdr_id = resp.data['id']
        provider_ref = resp.data['provider_reference']

        # 4. Verify 3,000 is reserved
        wallet.refresh_from_db()
        assert wallet.reserved_balance == 300000

        # 5. Verify available balance is 7,000
        assert wallet.available_balance == 700000

        # Verify initial ledger reservation entries
        res_tx = LedgerTransaction.objects.filter(reference_id=str(wdr_id), transaction_type=TransactionType.WITHDRAWAL_RESERVE).first()
        assert res_tx is not None
        assert res_tx.entries.count() == 2

        # 6. Trigger bank failure callback
        fail_callback_payload = {
            "provider_event_id": f"evt_fail_{uuid.uuid4().hex[:8]}",
            "provider_reference": provider_ref,
            "event_type": "PAYOUT_REJECTED",
            "reason": "Account closed at beneficiary bank"
        }
        signature = MockBankService.generate_signature(fail_callback_payload)

        client.credentials(HTTP_X_BANK_SIGNATURE=signature)
        cb_resp = client.post("/api/payments/callbacks/bank/", fail_callback_payload, format='json')
        assert cb_resp.status_code == 200
        assert cb_resp.data['status'] == 'acknowledged'

        # 7. Verify refund through compensating ledger entries
        refund_tx = LedgerTransaction.objects.filter(reference_id=str(wdr_id), transaction_type=TransactionType.WITHDRAWAL_REFUND).first()
        assert refund_tx is not None
        # Must have:
        # Debit: Customer Wallet Reserved (300,000)
        # Credit: Customer Wallet Available (300,000)
        entries = list(refund_tx.entries.all())
        assert len(entries) == 2
        debit_entry = [e for e in entries if e.direction == EntryDirection.DEBIT][0]
        credit_entry = [e for e in entries if e.direction == EntryDirection.CREDIT][0]
        assert debit_entry.amount == 300000
        assert credit_entry.amount == 300000

        # 8. Verify available balance returns to 10,000 and reserved balance is 0
        wallet.refresh_from_db()
        assert wallet.available_balance == 1000000
        assert wallet.reserved_balance == 0

        # Verify withdrawal state is FAILED
        wdr = Withdrawal.objects.get(id=wdr_id)
        assert wdr.status == WithdrawalStatus.FAILED

        # 9. Send the same failure callback again
        cb_resp2 = client.post("/api/payments/callbacks/bank/", fail_callback_payload, format='json')
        assert cb_resp2.status_code == 200

        # 10. Verify no second refund occurs
        wallet.refresh_from_db()
        assert wallet.available_balance == 1000000
        assert wallet.reserved_balance == 0
        assert LedgerTransaction.objects.filter(reference_id=str(wdr_id), transaction_type=TransactionType.WITHDRAWAL_REFUND).count() == 1

        # Final reconciliation audit
        report = ReconciliationService.reconcile_wallet(wallet)
        assert report.is_balanced is True

    def test_duplicate_success_callback_deduplication(self):
        """Verify repeated success webhooks do not double-settle or affect balances."""
        user = User.objects.create_user(username="dedup_user", email="dedup@wallet.local", password="Password123!")
        wallet = Wallet.objects.create(user=user, currency="INR")
        PaymentService.topup(wallet.id, 50000, "INR", user, "SUCCESS")

        client = APIClient()
        token = str(RefreshToken.for_user(user).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_IDEMPOTENCY_KEY="wdr_dedup_001")

        resp = client.post(f"/api/wallets/{wallet.id}/withdrawals/", {
            "amount": "200.00",
            "currency": "INR",
            "destination_bank_account": "HDFC_DEDUP"
        }, format='json')
        assert resp.status_code == 202
        wdr_id = resp.data['id']
        provider_ref = resp.data['provider_reference']

        success_payload = {
            "provider_event_id": f"evt_success_{uuid.uuid4().hex[:8]}",
            "provider_reference": provider_ref,
            "event_type": "PAYOUT_COMPLETED"
        }
        sig = MockBankService.generate_signature(success_payload)
        client.credentials(HTTP_X_BANK_SIGNATURE=sig)

        # First callback: settles payout
        resp1 = client.post("/api/payments/callbacks/bank/", success_payload, format='json')
        assert resp1.status_code == 200

        wallet.refresh_from_db()
        assert wallet.available_balance == 30000
        assert wallet.reserved_balance == 0

        # Second callback with exact same event ID: deduplicated
        resp2 = client.post("/api/payments/callbacks/bank/", success_payload, format='json')
        assert resp2.status_code == 200

        wallet.refresh_from_db()
        assert wallet.available_balance == 30000
        assert wallet.reserved_balance == 0
        assert LedgerTransaction.objects.filter(reference_id=str(wdr_id), transaction_type=TransactionType.WITHDRAWAL_SETTLE).count() == 1

    def test_conflicting_failure_callback_after_success_ignored(self):
        """If a bank sends a failure callback after payout already completed, ignore it."""
        user = User.objects.create_user(username="conflict_user", email="conflict@wallet.local", password="Password123!")
        wallet = Wallet.objects.create(user=user, currency="INR")
        PaymentService.topup(wallet.id, 50000, "INR", user, "SUCCESS")

        client = APIClient()
        token = str(RefreshToken.for_user(user).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_IDEMPOTENCY_KEY="wdr_conflict_001")

        resp = client.post(f"/api/wallets/{wallet.id}/withdrawals/", {
            "amount": "200.00",
            "currency": "INR",
            "destination_bank_account": "HDFC_CONFLICT"
        }, format='json')
        provider_ref = resp.data['provider_reference']

        # Complete success first
        succ_payload = {
            "provider_event_id": f"evt_succ_{uuid.uuid4().hex[:8]}",
            "provider_reference": provider_ref,
            "event_type": "PAYOUT_COMPLETED"
        }
        client.credentials(HTTP_X_BANK_SIGNATURE=MockBankService.generate_signature(succ_payload))
        client.post("/api/payments/callbacks/bank/", succ_payload, format='json')

        # Now send conflicting late failure callback with different event ID
        fail_payload = {
            "provider_event_id": f"evt_late_fail_{uuid.uuid4().hex[:8]}",
            "provider_reference": provider_ref,
            "event_type": "PAYOUT_REJECTED"
        }
        client.credentials(HTTP_X_BANK_SIGNATURE=MockBankService.generate_signature(fail_payload))
        resp = client.post("/api/payments/callbacks/bank/", fail_payload, format='json')
        assert resp.status_code == 200

        # Funds must NOT be refunded back to available
        wallet.refresh_from_db()
        assert wallet.available_balance == 30000
        assert wallet.reserved_balance == 0
        wdr = Withdrawal.objects.get(provider_reference=provider_ref)
        assert wdr.status == WithdrawalStatus.COMPLETED

    def test_withdrawal_restrictions_on_frozen_and_closed_wallets(self):
        user = User.objects.create_user(username="state_wdr_user", email="state_wdr@wallet.local", password="Password123!")
        wallet = Wallet.objects.create(user=user, currency="INR")
        PaymentService.topup(wallet.id, 50000, "INR", user, "SUCCESS")

        client = APIClient()
        token = str(RefreshToken.for_user(user).access_token)

        # 1. Frozen wallet cannot withdraw
        wallet.state = WalletState.FROZEN
        wallet.save()

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_IDEMPOTENCY_KEY="wdr_frozen_001")
        resp = client.post(f"/api/wallets/{wallet.id}/withdrawals/", {
            "amount": "100.00",
            "currency": "INR",
            "destination_bank_account": "HDFC_STATE"
        }, format='json')
        assert resp.status_code == 400
        assert "frozen" in str(resp.data).lower()

        # 2. Closed wallet cannot withdraw
        wallet.state = WalletState.CLOSED
        wallet.save()

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_IDEMPOTENCY_KEY="wdr_closed_001")
        resp = client.post(f"/api/wallets/{wallet.id}/withdrawals/", {
            "amount": "100.00",
            "currency": "INR",
            "destination_bank_account": "HDFC_STATE"
        }, format='json')
        assert resp.status_code == 400
        assert "closed" in str(resp.data).lower()

