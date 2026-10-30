import pytest
import uuid
from decimal import Decimal
from apps.accounts.models import User
from apps.wallets.models import Wallet
from apps.payments.services import PaymentService
from apps.payments.models import Withdrawal, WithdrawalStatus
from apps.payments.mock_bank import MockBankService
from apps.ledger.models import (
    LedgerAccount,
    LedgerTransaction,
    LedgerEntry,
    TransactionType,
    EntryDirection,
)
from apps.ledger.reconciliation import ReconciliationService


@pytest.mark.django_db
class TestReconciliationAllSixLifecycleEvents:
    """
    Validates Phase 3 requirements:
    Reconciliation test calculating wallet balance from immutable ledger entries
    and comparing against stored wallet balances across all 6 core lifecycle events:
    1. Top-up
    2. Transfer
    3. Withdrawal request
    4. Successful withdrawal
    5. Failed withdrawal and refund
    6. Transfer reversal
    """

    def test_reconciliation_across_all_six_lifecycle_events(self):
        admin_user = User.objects.create_superuser("admin_rec", "admin_rec@wallet.local", "Password123!")
        alice_user = User.objects.create_user("alice_rec", "alice_rec@wallet.local", "Password123!")
        bob_user = User.objects.create_user("bob_rec", "bob_rec@wallet.local", "Password123!")

        alice_wallet = Wallet.objects.create(user=alice_user, currency="INR")
        bob_wallet = Wallet.objects.create(user=bob_user, currency="INR")

        # EVENT 1: Top-up Alice ₹10,000.00 (1,000,000 paise)
        PaymentService.topup(
            wallet_id=alice_wallet.id,
            amount=1000000,
            currency="INR",
            user=alice_user,
            description="Event 1 Topup"
        )
        alice_wallet.refresh_from_db()
        rep1_alice = ReconciliationService.reconcile_wallet(alice_wallet)
        rep1_global = ReconciliationService.run_full_reconciliation()
        assert rep1_alice.is_balanced is True
        assert rep1_alice.expected_available == 1000000
        assert rep1_alice.cached_available == 1000000
        assert rep1_global.is_healthy is True

        # EVENT 2: Transfer ₹2,000.00 (200,000 paise) Alice -> Bob
        transfer = PaymentService.transfer(
            source_wallet_id=alice_wallet.id,
            dest_wallet_id=bob_wallet.id,
            amount=200000,
            currency="INR",
            user=alice_user,
            description="Event 2 Transfer"
        )
        alice_wallet.refresh_from_db()
        bob_wallet.refresh_from_db()
        rep2_alice = ReconciliationService.reconcile_wallet(alice_wallet)
        rep2_bob = ReconciliationService.reconcile_wallet(bob_wallet)
        rep2_global = ReconciliationService.run_full_reconciliation()
        assert rep2_alice.is_balanced is True
        assert rep2_alice.cached_available == 800000
        assert rep2_bob.is_balanced is True
        assert rep2_bob.cached_available == 200000
        assert rep2_global.is_healthy is True

        # EVENT 3: Withdrawal Request ₹1,000.00 (100,000 paise) on Bob
        wdr_job = PaymentService.request_withdrawal(
            wallet_id=bob_wallet.id,
            amount=100000,
            currency="INR",
            bank_account_info="HDFC_REC_01",
            user=bob_user
        )
        bob_wallet.refresh_from_db()
        rep3_bob = ReconciliationService.reconcile_wallet(bob_wallet)
        rep3_global = ReconciliationService.run_full_reconciliation()
        assert rep3_bob.is_balanced is True
        assert rep3_bob.cached_available == 100000
        assert rep3_bob.cached_reserved == 100000
        assert rep3_global.is_healthy is True

        # EVENT 4: Successful Withdrawal Callback (Settle Bob's ₹1,000.00)
        succ_payload = {
            "provider_event_id": f"evt_succ_{uuid.uuid4().hex[:8]}",
            "provider_reference": wdr_job.provider_reference,
            "event_type": "PAYOUT_COMPLETED"
        }
        sig = MockBankService.generate_signature(succ_payload)
        success, _ = PaymentService.process_bank_callback(succ_payload, sig)
        assert success is True

        bob_wallet.refresh_from_db()
        rep4_bob = ReconciliationService.reconcile_wallet(bob_wallet)
        rep4_global = ReconciliationService.run_full_reconciliation()
        assert rep4_bob.is_balanced is True
        assert rep4_bob.cached_available == 100000
        assert rep4_bob.cached_reserved == 0
        assert rep4_global.is_healthy is True

        # EVENT 5: Failed Withdrawal & Compensating Refund
        # Alice requests ₹500.00 (50,000 paise) withdrawal, bank rejects
        wdr_fail_job = PaymentService.request_withdrawal(
            wallet_id=alice_wallet.id,
            amount=50000,
            currency="INR",
            bank_account_info="HDFC_REC_FAIL",
            user=alice_user
        )
        alice_wallet.refresh_from_db()
        assert alice_wallet.available_balance == 750000
        assert alice_wallet.reserved_balance == 50000

        fail_payload = {
            "provider_event_id": f"evt_fail_{uuid.uuid4().hex[:8]}",
            "provider_reference": wdr_fail_job.provider_reference,
            "event_type": "PAYOUT_REJECTED",
            "reason": "Account invalid"
        }
        fail_sig = MockBankService.generate_signature(fail_payload)
        success_fail, _ = PaymentService.process_bank_callback(fail_payload, fail_sig)
        assert success_fail is True

        alice_wallet.refresh_from_db()
        rep5_alice = ReconciliationService.reconcile_wallet(alice_wallet)
        rep5_global = ReconciliationService.run_full_reconciliation()
        assert rep5_alice.is_balanced is True
        assert rep5_alice.cached_available == 800000
        assert rep5_alice.cached_reserved == 0
        assert rep5_global.is_healthy is True

        # EVENT 6: Transfer Reversal (Reversing the ₹2,000.00 transfer from Event 2)
        # Note: Bob currently has 100,000 paise. To reverse a 200,000 paise transfer,
        # Bob needs sufficient funds (solvency invariant). Let's fund Bob with 150,000 paise so Bob has >= 200,000 paise.
        PaymentService.topup(bob_wallet.id, 150000, "INR", bob_user, "SUCCESS")
        bob_wallet.refresh_from_db()
        assert bob_wallet.available_balance == 250000

        # Now execute reversal by admin
        PaymentService.reverse_transfer(
            transfer_id=transfer.id,
            admin_user=admin_user,
            reason="Customer dispute reversal Event 6"
        )
        alice_wallet.refresh_from_db()
        bob_wallet.refresh_from_db()

        rep6_alice = ReconciliationService.reconcile_wallet(alice_wallet)
        rep6_bob = ReconciliationService.reconcile_wallet(bob_wallet)
        rep6_global = ReconciliationService.run_full_reconciliation()

        assert rep6_alice.is_balanced is True
        assert rep6_alice.cached_available == 1000000  # 800,000 + 200,000 clawed back = 1,000,000
        assert rep6_bob.is_balanced is True
        assert rep6_bob.cached_available == 50000     # 250,000 - 200,000 reversed = 50,000
        assert rep6_global.is_healthy is True

        # Global sum of all debits across all INR entries must equal global sum of all credits
        inr_summary = rep6_global.currency_balances['INR']
        assert inr_summary['debits'] == inr_summary['credits']
        assert inr_summary['debits'] > 0
