import pytest
import uuid
from rest_framework import status
from apps.payments.services import PaymentService


@pytest.mark.django_db
def test_daily_outgoing_limit_enforced(client_a, wallet_a_inr, wallet_b_inr, user_a):
    # Set custom daily limit of 500.00 INR (50,000 minor units) on wallet A
    wallet_a_inr.daily_limit = 50000
    wallet_a_inr.save()

    # Fund wallet with 1,000.00 INR
    PaymentService.topup(wallet_a_inr.id, 100000, "INR", user_a)

    headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    # 1. Outgoing transfer of 300.00 INR (allowed)
    res1 = client_a.post('/api/transfers/', {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '300.00',
        'currency': 'INR'
    }, format='json', **headers)
    assert res1.status_code == status.HTTP_201_CREATED

    # 2. Outgoing transfer of 250.00 INR (300 + 250 = 550 > 500 limit: blocked!)
    headers2 = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    res2 = client_a.post('/api/transfers/', {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '250.00',
        'currency': 'INR'
    }, format='json', **headers2)
    assert res2.status_code == status.HTTP_400_BAD_REQUEST
    assert res2.data['error']['code'] == 'DAILY_LIMIT_EXCEEDED'
    assert "limit" in res2.data['error']['message'].lower()
