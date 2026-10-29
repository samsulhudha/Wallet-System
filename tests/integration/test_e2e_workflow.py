import pytest
import uuid
from decimal import Decimal
from django.core.management import call_command
from rest_framework.test import APIClient
from rest_framework import status
from apps.accounts.models import User
from apps.wallets.models import Wallet, WalletState
from apps.payments.models import Withdrawal, WithdrawalStatus, Transfer
from apps.ledger.reconciliation import ReconciliationService


@pytest.mark.django_db(transaction=True)
def test_full_e2e_customer_and_admin_lifecycle(admin_user):
    """
    End-to-End (E2E) Test:
    Executes complete realistic workflow from user registration through funding,
    idempotent retries, P2P transfers, async withdrawals, bank callbacks,
    fraud checks, admin freeze/unfreeze, transfer reversal, statement checking,
    and mathematical ledger reconciliation.
    """
    anon_client = APIClient()

    # --------------------------------------------------------------------------
    # Step 1: Customer Registration (Alice & Bob)
    # --------------------------------------------------------------------------
    alice_reg = anon_client.post('/api/auth/register/', {
        'username': 'alice_e2e',
        'email': 'alice.e2e@example.com',
        'password': 'StrongPassword123!',
        'password_confirm': 'StrongPassword123!'
    }, format='json')
    assert alice_reg.status_code == status.HTTP_201_CREATED

    bob_reg = anon_client.post('/api/auth/register/', {
        'username': 'bob_e2e',
        'email': 'bob.e2e@example.com',
        'password': 'StrongPassword123!',
        'password_confirm': 'StrongPassword123!'
    }, format='json')
    assert bob_reg.status_code == status.HTTP_201_CREATED

    # --------------------------------------------------------------------------
    # Step 2: Authentication (JWT Token Generation)
    # --------------------------------------------------------------------------
    alice_token_res = anon_client.post('/api/auth/token/', {
        'username': 'alice_e2e',
        'password': 'StrongPassword123!'
    }, format='json')
    assert alice_token_res.status_code == status.HTTP_200_OK
    alice_token = alice_token_res.data['access']

    bob_token_res = anon_client.post('/api/auth/token/', {
        'username': 'bob_e2e',
        'password': 'StrongPassword123!'
    }, format='json')
    assert bob_token_res.status_code == status.HTTP_200_OK
    bob_token = bob_token_res.data['access']

    admin_token_res = anon_client.post('/api/auth/token/', {
        'username': admin_user.username,
        'password': 'AdminPassword123!'
    }, format='json')
    assert admin_token_res.status_code == status.HTTP_200_OK
    admin_token = admin_token_res.data['access']

    alice_client = APIClient()
    alice_client.credentials(HTTP_AUTHORIZATION=f'Bearer {alice_token}')

    bob_client = APIClient()
    bob_client.credentials(HTTP_AUTHORIZATION=f'Bearer {bob_token}')

    admin_client = APIClient()
    admin_client.credentials(HTTP_AUTHORIZATION=f'Bearer {admin_token}')

    # --------------------------------------------------------------------------
    # Step 3: Wallet Creation (INR)
    # --------------------------------------------------------------------------
    alice_wallet_res = alice_client.post('/wallets/', {'currency': 'INR'}, format='json')
    assert alice_wallet_res.status_code == status.HTTP_201_CREATED
    alice_wallet_id = alice_wallet_res.data['id']

    bob_wallet_res = bob_client.post('/wallets/', {'currency': 'INR'}, format='json')
    assert bob_wallet_res.status_code == status.HTTP_201_CREATED
    bob_wallet_id = bob_wallet_res.data['id']

    # Initial balances are 0.00
    res_a = alice_client.get(f'/wallets/{alice_wallet_id}/')
    assert res_a.data['available_balance_decimal'] == '0.00'
    assert res_a.data['reserved_balance_decimal'] == '0.00'

    # --------------------------------------------------------------------------
    # Step 4: Top-Up with Idempotency Protection
    # --------------------------------------------------------------------------
    topup_key = str(uuid.uuid4())
    topup_headers = {'HTTP_IDEMPOTENCY_KEY': topup_key}
    topup_payload = {'amount': '1000.00', 'currency': 'INR'}

    # 4a. Execute top-up of 1,000.00 INR
    topup_res1 = alice_client.post(f'/wallets/{alice_wallet_id}/topups/', topup_payload, format='json', **topup_headers)
    assert topup_res1.status_code == status.HTTP_201_CREATED
    assert topup_res1.data['amount'] == 100000

    # 4b. Idempotent Retry: Same key returns original response and does NOT double-credit
    topup_res2 = alice_client.post(f'/wallets/{alice_wallet_id}/topups/', topup_payload, format='json', **topup_headers)
    assert topup_res2.status_code == status.HTTP_201_CREATED
    assert topup_res2.data['id'] == topup_res1.data['id']

    res_a_after_topup = alice_client.get(f'/wallets/{alice_wallet_id}/')
    assert res_a_after_topup.data['available_balance_decimal'] == '1000.00'

    # --------------------------------------------------------------------------
    # Step 5: Atomic P2P Transfer (Alice -> Bob)
    # --------------------------------------------------------------------------
    transfer_key = str(uuid.uuid4())
    transfer_headers = {'HTTP_IDEMPOTENCY_KEY': transfer_key}
    transfer_payload = {
        'source_wallet_id': alice_wallet_id,
        'destination_wallet_id': bob_wallet_id,
        'amount': '300.00',
        'currency': 'INR',
        'description': 'Consulting payment'
    }

    transfer_res = alice_client.post('/transfers/', transfer_payload, format='json', **transfer_headers)
    assert transfer_res.status_code == status.HTTP_201_CREATED
    transfer_id = transfer_res.data['id']
    assert transfer_res.data['amount'] == 30000

    # Verify balances: Alice has 700.00, Bob has 300.00
    res_a_post_transfer = alice_client.get(f'/wallets/{alice_wallet_id}/')
    res_b_post_transfer = bob_client.get(f'/wallets/{bob_wallet_id}/')
    assert res_a_post_transfer.data['available_balance_decimal'] == '700.00'
    assert res_b_post_transfer.data['available_balance_decimal'] == '300.00'

    # --------------------------------------------------------------------------
    # Step 6: Asynchronous Bank Withdrawal (Bob -> Bank)
    # --------------------------------------------------------------------------
    wdr_key = str(uuid.uuid4())
    wdr_headers = {'HTTP_IDEMPOTENCY_KEY': wdr_key}
    wdr_payload = {
        'amount': '100.00',
        'currency': 'INR',
        'bank_account_info': 'ICICI_BANK_9988776655',
        'simulated_outcome': 'SUCCESS'
    }

    wdr_res = bob_client.post(f'/wallets/{bob_wallet_id}/withdrawals/', wdr_payload, format='json', **wdr_headers)
    assert wdr_res.status_code == status.HTTP_202_ACCEPTED
    wdr_id = wdr_res.data['id']
    assert wdr_res.data['status'] == WithdrawalStatus.REQUESTED

    # Verify funds reserved immediately: Bob has Available: 200.00, Reserved: 100.00
    res_b_reserved = bob_client.get(f'/wallets/{bob_wallet_id}/')
    assert res_b_reserved.data['available_balance_decimal'] == '200.00'
    assert res_b_reserved.data['reserved_balance_decimal'] == '100.00'

    # Run background mock bank worker
    call_command('process_withdrawals', '--once')

    # Verify settled: Available: 200.00, Reserved: 0.00, Status: COMPLETED
    res_b_settled = bob_client.get(f'/wallets/{bob_wallet_id}/')
    assert res_b_settled.data['available_balance_decimal'] == '200.00'
    assert res_b_settled.data['reserved_balance_decimal'] == '0.00'

    wdr_detail = bob_client.get(f'/withdrawals/{wdr_id}/')
    assert wdr_detail.data['status'] == WithdrawalStatus.COMPLETED

    # --------------------------------------------------------------------------
    # Step 7: Withdrawal Failure & Compensating Refund (Alice)
    # --------------------------------------------------------------------------
    wdr_fail_key = str(uuid.uuid4())
    wdr_fail_payload = {
        'amount': '200.00',
        'currency': 'INR',
        'bank_account_info': 'INVALID_ACCOUNT_000',
        'simulated_outcome': 'REJECTION'
    }

    wdr_fail_res = alice_client.post(
        f'/wallets/{alice_wallet_id}/withdrawals/',
        wdr_fail_payload,
        format='json',
        HTTP_IDEMPOTENCY_KEY=wdr_fail_key
    )
    assert wdr_fail_res.status_code == status.HTTP_202_ACCEPTED
    wdr_fail_id = wdr_fail_res.data['id']

    # Funds temporarily reserved: Available: 500.00, Reserved: 200.00
    res_a_fail_reserved = alice_client.get(f'/wallets/{alice_wallet_id}/')
    assert res_a_fail_reserved.data['available_balance_decimal'] == '500.00'
    assert res_a_fail_reserved.data['reserved_balance_decimal'] == '200.00'

    # Run worker to process rejection
    call_command('process_withdrawals', '--once')

    # Funds refunded automatically: Available restored to 700.00, Reserved: 0.00
    res_a_refunded = alice_client.get(f'/wallets/{alice_wallet_id}/')
    assert res_a_refunded.data['available_balance_decimal'] == '700.00'
    assert res_a_refunded.data['reserved_balance_decimal'] == '0.00'

    wdr_fail_detail = alice_client.get(f'/withdrawals/{wdr_fail_id}/')
    assert wdr_fail_detail.data['status'] == WithdrawalStatus.FAILED

    # --------------------------------------------------------------------------
    # Step 8: Admin Flags Inspection
    # --------------------------------------------------------------------------
    flags_res = admin_client.get('/admin/flags/')
    assert flags_res.status_code == status.HTTP_200_OK
    assert 'results' in flags_res.data or isinstance(flags_res.data, list)

    # --------------------------------------------------------------------------
    # Step 9: Wallet State Management (Freeze -> Block Transfer -> Unfreeze)
    # --------------------------------------------------------------------------
    # 9a. Freeze Alice's wallet
    freeze_res = admin_client.post(
        f'/api/admin/wallets/{alice_wallet_id}/freeze/',
        {'reason': 'Temporary security freeze'},
        format='json'
    )
    assert freeze_res.status_code == status.HTTP_200_OK
    assert freeze_res.data['state'] == WalletState.FROZEN

    # 9b. Alice attempts outgoing transfer while frozen: MUST be rejected!
    blocked_transfer_res = alice_client.post(
        '/transfers/',
        {
            'source_wallet_id': alice_wallet_id,
            'destination_wallet_id': bob_wallet_id,
            'amount': '50.00',
            'currency': 'INR'
        },
        format='json',
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
    )
    assert blocked_transfer_res.status_code == status.HTTP_400_BAD_REQUEST
    assert "frozen" in blocked_transfer_res.data['error']['message'].lower()

    # 9c. Unfreeze Alice's wallet
    unfreeze_res = admin_client.post(
        f'/api/admin/wallets/{alice_wallet_id}/unfreeze/',
        {'reason': 'Security verification completed'},
        format='json'
    )
    assert unfreeze_res.status_code == status.HTTP_200_OK
    assert unfreeze_res.data['state'] == WalletState.ACTIVE

    # --------------------------------------------------------------------------
    # Step 10: Admin Reversal with Recipient Balance Verification
    # --------------------------------------------------------------------------
    # Bob currently has 200.00 INR. If admin attempts to reverse 300.00 INR now,
    # the policy must reject it with HTTP 400 because recipient has insufficient funds!
    insufficient_rev_res = admin_client.post(
        f'/api/admin/transfers/{transfer_id}/reverse/',
        {'reason': 'Premature clawback attempt'},
        format='json',
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
    )
    assert insufficient_rev_res.status_code == status.HTTP_400_BAD_REQUEST
    assert "insufficient" in insufficient_rev_res.data['error']['message'].lower()

    # Bob funds his wallet with 300.00 INR so he has 500.00 INR total
    bob_client.post(
        f'/wallets/{bob_wallet_id}/topups/',
        {'amount': '300.00', 'currency': 'INR'},
        format='json',
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
    )

    # Now the reversal succeeds cleanly!
    rev_res = admin_client.post(
        f'/api/admin/transfers/{transfer_id}/reverse/',
        {'reason': 'Dispute resolved in Alice favor'},
        format='json',
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
    )
    assert rev_res.status_code == status.HTTP_200_OK
    assert rev_res.data['is_reversed'] is True

    # Balances: Alice 700 + 300 = 1000.00 INR, Bob 500 - 300 = 200.00 INR
    res_a_final = alice_client.get(f'/wallets/{alice_wallet_id}/')
    res_b_final = bob_client.get(f'/wallets/{bob_wallet_id}/')
    assert res_a_final.data['available_balance_decimal'] == '1000.00'
    assert res_b_final.data['available_balance_decimal'] == '200.00'

    # --------------------------------------------------------------------------
    # Step 11: Statement Checking & Ledger Invariant Audit
    # --------------------------------------------------------------------------
    stmt_res = alice_client.get(f'/wallets/{alice_wallet_id}/transactions/?page_size=100')
    assert stmt_res.status_code == status.HTTP_200_OK
    entries = stmt_res.data['results']
    assert len(entries) >= 5

    # Compute Alice's balance by summing all ledger statement entries
    computed_balance = 0
    for e in entries:
        if e['account_type'] == 'CUSTOMER_WALLET_AVAILABLE':
            if e['direction'] == 'CREDIT':
                computed_balance += e['amount']
            elif e['direction'] == 'DEBIT':
                computed_balance -= e['amount']

    # Computed balance from statement must match 1,000.00 INR (100,000 minor units) exactly!
    assert computed_balance == 100000

    # --------------------------------------------------------------------------
    # Step 12: Global Double-Entry Ledger Reconciliation
    # --------------------------------------------------------------------------
    global_report = ReconciliationService.run_full_reconciliation()
    assert global_report.is_healthy is True
    assert global_report.discrepant_wallets_count == 0
    assert global_report.unbalanced_transactions_count == 0
