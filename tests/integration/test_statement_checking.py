import pytest
import uuid
from datetime import timedelta
from django.utils import timezone
from rest_framework import status
from apps.wallets.models import Wallet
from apps.payments.services import PaymentService
from apps.payments.mock_bank import MockBankService
from apps.common.money import minor_units_to_decimal


@pytest.mark.django_db(transaction=True)
def test_statement_based_balance_calculation_and_audit(client_a, client_b, wallet_a_inr, wallet_b_inr, user_a, user_b, admin_user):
    """
    Statement-Based Checking:
    Executes a comprehensive sequence of real-world money movements:
      1. Top-up 1,000.00 INR (+100,000 minor units)
      2. Outgoing transfer of 200.00 INR (-20,000 minor units) to Bob
      3. Incoming transfer of 50.00 INR (+5,000 minor units) from Bob
      4. Successful withdrawal of 100.00 INR (-10,000 minor units settled to bank)
      5. Failed withdrawal of 150.00 INR (-15,000 reserved, then +15,000 refunded)
      6. Administrative reversal of transfer #1 (+20,000 minor units restored)

    Verifies:
      - All entries appear in GET /wallets/{id}/transactions/
      - Calculating sum(Credits) - sum(Debits) for Available Account directly from
        the API statement response matches wallet.available_balance to the exact penny!
      - Calculating sum(Credits) - sum(Debits) for Reserved Account directly from
        the API statement response matches wallet.reserved_balance (0)!
    """
    # 1. Top-up 1,000.00 INR
    PaymentService.topup(wallet_a_inr.id, 100000, "INR", user_a)

    # 2. Outgoing transfer of 200.00 INR to Bob
    tx1 = PaymentService.transfer(wallet_a_inr.id, wallet_b_inr.id, 20000, "INR", user_a, description="Transfer to Bob")

    # Fund Bob so Bob can transfer back
    PaymentService.topup(wallet_b_inr.id, 50000, "INR", user_b)

    # 3. Incoming transfer of 50.00 INR from Bob
    PaymentService.transfer(wallet_b_inr.id, wallet_a_inr.id, 5000, "INR", user_b, description="Bob return share")

    # 4. Successful withdrawal of 100.00 INR
    wdr_success = PaymentService.request_withdrawal(wallet_a_inr.id, 10000, "INR", user_a)
    cb_success_payload = {
        "provider_event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "provider_reference": wdr_success.provider_reference,
        "event_type": "PAYOUT_COMPLETED",
        "amount": 10000,
        "currency": "INR"
    }
    sig_success = MockBankService.generate_signature(cb_success_payload)
    PaymentService.process_bank_callback(cb_success_payload, sig_success)

    # 5. Failed withdrawal of 150.00 INR
    wdr_fail = PaymentService.request_withdrawal(wallet_a_inr.id, 15000, "INR", user_a)
    cb_fail_payload = {
        "provider_event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "provider_reference": wdr_fail.provider_reference,
        "event_type": "PAYOUT_REJECTED",
        "amount": 15000,
        "currency": "INR",
        "failure_reason": "Account closed"
    }
    sig_fail = MockBankService.generate_signature(cb_fail_payload)
    PaymentService.process_bank_callback(cb_fail_payload, sig_fail)

    # 6. Administrative reversal of transfer #1 (recovers 20,000 from Bob back to Alice)
    PaymentService.reverse_transfer(tx1.id, admin_user, reason="Fraud dispute resolution")

    # Expected live balances:
    # Topup (+100,000) - Transfer (-20,000) + InTransfer (+5,000) - WdrSuccess (-10,000)
    # - WdrFailReserve (-15,000) + WdrFailRefund (+15,000) + Reversal (+20,000)
    # Total Available = 100,000 - 20,000 + 5,000 - 10,000 + 20,000 = 95,000 minor units (950.00 INR)
    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 95000
    assert wallet_a_inr.reserved_balance == 0

    # Fetch statement via API (testing both root and /api/ paths)
    res = client_a.get(f'/wallets/{wallet_a_inr.id}/transactions/?page_size=100')
    assert res.status_code == status.HTTP_200_OK

    statement_entries = res.data['results']
    assert len(statement_entries) >= 7

    # Compute balances strictly by summing statement entries returned by the API
    statement_available_balance = 0
    statement_reserved_balance = 0

    for entry in statement_entries:
        amt = entry['amount']
        direction = entry['direction']
        acc_type = entry['account_type']

        if acc_type == 'CUSTOMER_WALLET_AVAILABLE':
            if direction == 'CREDIT':
                statement_available_balance += amt
            elif direction == 'DEBIT':
                statement_available_balance -= amt
        elif acc_type == 'CUSTOMER_WALLET_RESERVED':
            if direction == 'CREDIT':
                statement_reserved_balance += amt
            elif direction == 'DEBIT':
                statement_reserved_balance -= amt

    # Statement calculation MUST match database wallet balance exactly!
    assert statement_available_balance == wallet_a_inr.available_balance == 95000
    assert statement_reserved_balance == wallet_a_inr.reserved_balance == 0


@pytest.mark.django_db(transaction=True)
def test_statement_filtering_by_date_and_type(client_a, wallet_a_inr, wallet_b_inr, user_a):
    """
    Verifies that date range filters (start_date, end_date) and transaction_type
    filter queries return the exact expected ledger transactions.
    """
    now = timezone.now()

    # 1. Top-up
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)

    # 2. Transfer
    PaymentService.transfer(wallet_a_inr.id, wallet_b_inr.id, 10000, "INR", user_a)

    # Filter by transaction_type=TOPUP
    res_topup = client_a.get(f'/api/wallets/{wallet_a_inr.id}/transactions/?transaction_type=TOPUP')
    assert res_topup.status_code == status.HTTP_200_OK
    assert all(e['transaction_type'] == 'TOPUP' for e in res_topup.data['results'])
    assert len(res_topup.data['results']) >= 1

    # Filter by transaction_type=TRANSFER
    res_transfer = client_a.get(f'/api/wallets/{wallet_a_inr.id}/transactions/?transaction_type=TRANSFER')
    assert res_transfer.status_code == status.HTTP_200_OK
    assert all(e['transaction_type'] == 'TRANSFER' for e in res_transfer.data['results'])
    assert len(res_transfer.data['results']) >= 1

    # Filter by date range (future date returns 0)
    future_start = (now + timedelta(days=5)).isoformat()
    res_future = client_a.get(f'/api/wallets/{wallet_a_inr.id}/transactions/?start_date={future_start}')
    assert res_future.status_code == status.HTTP_200_OK
    assert len(res_future.data['results']) == 0

    # Filter by past date window covering current transactions
    past_start = (now - timedelta(hours=1)).isoformat()
    future_end = (now + timedelta(hours=1)).isoformat()
    res_window = client_a.get(f'/api/wallets/{wallet_a_inr.id}/transactions/?start_date={past_start}&end_date={future_end}')
    assert res_window.status_code == status.HTTP_200_OK
    assert len(res_window.data['results']) >= 2

