import pytest
import uuid
from rest_framework import status
from django.core.management import call_command
from apps.wallets.models import Wallet
from apps.payments.models import Withdrawal, WithdrawalStatus, WithdrawalJob, WithdrawalJobStatus
from apps.payments.services import PaymentService


@pytest.mark.django_db
def test_withdrawal_reservation_and_job_creation(client_a, wallet_a_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)  # 500.00 INR

    headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    payload = {
        'amount': '200.00',
        'currency': 'INR',
        'bank_account_info': 'HDFC0001234_987654321',
        'simulated_outcome': 'SUCCESS'
    }

    res = client_a.post(f'/api/wallets/{wallet_a_inr.id}/withdrawals/', payload, format='json', **headers)
    assert res.status_code == status.HTTP_202_ACCEPTED
    assert res.data['status'] == WithdrawalStatus.REQUESTED
    assert res.data['amount'] == 20000

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 30000
    assert wallet_a_inr.reserved_balance == 20000

    # Job is pending in queue
    job = WithdrawalJob.objects.filter(withdrawal_id=res.data['id']).first()
    assert job is not None
    assert job.status == WithdrawalJobStatus.PENDING

    # Run the worker to process job
    call_command('process_withdrawals', '--once')

    # Status should now be COMPLETED
    withdrawal = Withdrawal.objects.get(id=res.data['id'])
    assert withdrawal.status == WithdrawalStatus.COMPLETED

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 30000
    assert wallet_a_inr.reserved_balance == 0  # Settled to bank


@pytest.mark.django_db
def test_withdrawal_failure_refunds_reserved_balance(client_a, wallet_a_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)

    headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    payload = {
        'amount': '150.00',
        'currency': 'INR',
        'bank_account_info': 'HDFC0001234_987654321',
        'simulated_outcome': 'REJECTION'
    }

    res = client_a.post(f'/api/wallets/{wallet_a_inr.id}/withdrawals/', payload, format='json', **headers)
    assert res.status_code == status.HTTP_202_ACCEPTED

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 35000
    assert wallet_a_inr.reserved_balance == 15000

    # Process job with failure
    call_command('process_withdrawals', '--once')

    withdrawal = Withdrawal.objects.get(id=res.data['id'])
    assert withdrawal.status == WithdrawalStatus.FAILED

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 50000  # Refunded!
    assert wallet_a_inr.reserved_balance == 0
