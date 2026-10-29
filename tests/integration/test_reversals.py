import pytest
import uuid
from rest_framework import status
from apps.wallets.models import Wallet
from apps.payments.models import Transfer, TransferStatus
from apps.payments.services import PaymentService


@pytest.mark.django_db
def test_admin_transfer_reversal_success(admin_client, client_a, wallet_a_inr, wallet_b_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    transfer = PaymentService.transfer(wallet_a_inr.id, wallet_b_inr.id, 20000, "INR", user_a)

    wallet_a_inr.refresh_from_db()
    wallet_b_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 30000
    assert wallet_b_inr.available_balance == 20000

    # Non-admin cannot reverse
    headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    res_unauth = client_a.post(
        f'/api/admin/transfers/{transfer.id}/reverse/',
        {'reason': 'Unauthorized try'},
        format='json',
        **headers
    )
    assert res_unauth.status_code == status.HTTP_403_FORBIDDEN

    # Admin reverses
    res_admin = admin_client.post(
        f'/api/admin/transfers/{transfer.id}/reverse/',
        {'reason': 'Verified transaction dispute'},
        format='json',
        **headers
    )
    assert res_admin.status_code == status.HTTP_200_OK
    assert res_admin.data['is_reversed'] is True

    wallet_a_inr.refresh_from_db()
    wallet_b_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 50000
    assert wallet_b_inr.available_balance == 0

    # Cannot reverse a transfer twice
    headers2 = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    res_dup = admin_client.post(
        f'/api/admin/transfers/{transfer.id}/reverse/',
        {'reason': 'Second try'},
        format='json',
        **headers2
    )
    assert res_dup.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_reversal_rejected_when_recipient_has_insufficient_funds(admin_client, wallet_a_inr, wallet_b_inr, user_a, user_b):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    transfer = PaymentService.transfer(wallet_a_inr.id, wallet_b_inr.id, 40000, "INR", user_a)

    # Recipient spends their funds on a withdrawal
    PaymentService.request_withdrawal(wallet_b_inr.id, 35000, "INR", user_b)
    wallet_b_inr.refresh_from_db()
    assert wallet_b_inr.available_balance == 5000  # Only 5,000 available now

    # Reversal attempts to claw back 40,000 from B
    headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    res = admin_client.post(
        f'/api/admin/transfers/{transfer.id}/reverse/',
        {'reason': 'Clawback attempt'},
        format='json',
        **headers
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "insufficient available balance" in res.data['error']['message'].lower()
