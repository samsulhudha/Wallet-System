import pytest
from rest_framework import status
from apps.payments.services import PaymentService


@pytest.mark.django_db
def test_statements_and_pagination(client_a, client_b, wallet_a_inr, wallet_b_inr, user_a):
    # Perform topup, transfer, and withdrawal on wallet A
    PaymentService.topup(wallet_a_inr.id, 100000, "INR", user_a)
    PaymentService.transfer(wallet_a_inr.id, wallet_b_inr.id, 25000, "INR", user_a)
    PaymentService.request_withdrawal(wallet_a_inr.id, 10000, "INR", user_a)

    # 1. Fetch statements
    res = client_a.get(f'/api/wallets/{wallet_a_inr.id}/transactions/')
    assert res.status_code == status.HTTP_200_OK
    assert 'results' in res.data
    assert len(res.data['results']) >= 3

    # Verify no private PII of counterparties leaked
    for item in res.data['results']:
        assert 'password' not in item
        assert 'email' not in item
        assert 'account_type' in item
        assert 'direction' in item
        assert 'amount_decimal' in item

    # 2. Filter by transaction type
    res_topup = client_a.get(f'/api/wallets/{wallet_a_inr.id}/transactions/?transaction_type=TOPUP')
    assert res_topup.status_code == status.HTTP_200_OK
    for item in res_topup.data['results']:
        assert item['transaction_type'] == 'TOPUP'

    # 3. User B cannot view User A's statements
    res_unauth = client_b.get(f'/api/wallets/{wallet_a_inr.id}/transactions/')
    assert res_unauth.status_code == status.HTTP_403_FORBIDDEN

