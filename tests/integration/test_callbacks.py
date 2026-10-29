import pytest
import uuid
from rest_framework import status
from apps.wallets.models import Wallet
from apps.payments.models import Withdrawal, WithdrawalStatus, BankCallbackLog
from apps.payments.mock_bank import MockBankService
from apps.payments.services import PaymentService


@pytest.mark.django_db
def test_bank_callback_hmac_and_deduplication(api_client, wallet_a_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    withdrawal = PaymentService.request_withdrawal(wallet_a_inr.id, 20000, "INR", user_a)

    callback_payload = {
        "provider_event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "provider_reference": withdrawal.provider_reference,
        "event_type": "PAYOUT_COMPLETED",
        "amount": 20000,
        "currency": "INR"
    }

    # 1. Invalid signature rejected
    bad_headers = {'HTTP_X_BANK_SIGNATURE': 'invalid_signature_hash'}
    res_bad = api_client.post('/api/payments/callbacks/bank/', callback_payload, format='json', **bad_headers)
    assert res_bad.status_code == status.HTTP_400_BAD_REQUEST

    # 2. Valid signature accepted
    valid_sig = MockBankService.generate_signature(callback_payload)
    valid_headers = {'HTTP_X_BANK_SIGNATURE': valid_sig}

    res_good = api_client.post('/api/payments/callbacks/bank/', callback_payload, format='json', **valid_headers)
    assert res_good.status_code == status.HTTP_200_OK

    withdrawal.refresh_from_db()
    assert withdrawal.status == WithdrawalStatus.COMPLETED

    # 3. Duplicate callback safely acknowledged without double processing
    res_dup = api_client.post('/api/payments/callbacks/bank/', callback_payload, format='json', **valid_headers)
    assert res_dup.status_code == status.HTTP_200_OK
    assert "duplicate" in res_dup.data['message'].lower()


@pytest.mark.django_db
def test_late_callback_on_finalized_withdrawal(api_client, wallet_a_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    withdrawal = PaymentService.request_withdrawal(wallet_a_inr.id, 20000, "INR", user_a)
    withdrawal.status = WithdrawalStatus.COMPLETED
    withdrawal.save()

    callback_payload = {
        "provider_event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "provider_reference": withdrawal.provider_reference,
        "event_type": "PAYOUT_REJECTED",
        "amount": 20000,
        "currency": "INR"
    }

    sig = MockBankService.generate_signature(callback_payload)
    res = api_client.post('/api/payments/callbacks/bank/', callback_payload, format='json', HTTP_X_BANK_SIGNATURE=sig)
    assert res.status_code == status.HTTP_200_OK
    assert "already finalized" in res.data['message'].lower()

