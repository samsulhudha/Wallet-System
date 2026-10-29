import pytest
from rest_framework import status
from apps.wallets.models import Wallet, WalletState


@pytest.mark.django_db
def test_create_and_retrieve_wallet(client_a, user_a):
    # 1. Create wallet
    res = client_a.post('/api/wallets/', {'currency': 'INR'}, format='json')
    assert res.status_code == status.HTTP_201_CREATED
    wallet_id = res.data['id']
    assert res.data['currency'] == 'INR'
    assert res.data['available_balance_decimal'] == '0.00'

    # 2. Duplicate wallet for same user and currency rejected
    res_dup = client_a.post('/api/wallets/', {'currency': 'INR'}, format='json')
    assert res_dup.status_code == status.HTTP_400_BAD_REQUEST

    # 3. Retrieve wallet
    res_get = client_a.get(f'/api/wallets/{wallet_id}/')
    assert res_get.status_code == status.HTTP_200_OK
    assert res_get.data['id'] == wallet_id
    assert res_get.data['owner_username'] == user_a.username


@pytest.mark.django_db
def test_wallet_object_permissions(client_a, client_b, admin_client, wallet_a_inr):
    # User B cannot access User A's wallet
    res_b = client_b.get(f'/api/wallets/{wallet_a_inr.id}/')
    assert res_b.status_code == status.HTTP_403_FORBIDDEN

    # Owner can access
    res_a = client_a.get(f'/api/wallets/{wallet_a_inr.id}/')
    assert res_a.status_code == status.HTTP_200_OK

    # Admin can access
    res_admin = admin_client.get(f'/api/wallets/{wallet_a_inr.id}/')
    assert res_admin.status_code == status.HTTP_200_OK

