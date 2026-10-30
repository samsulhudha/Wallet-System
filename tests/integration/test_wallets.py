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


@pytest.mark.django_db
def test_multiple_currencies_handled_independently(client_a, user_a):
    # User can create INR wallet
    res_inr = client_a.post('/api/wallets/', {'currency': 'INR'}, format='json')
    assert res_inr.status_code == status.HTTP_201_CREATED

    # Same user can also create USD wallet
    res_usd = client_a.post('/api/wallets/', {'currency': 'USD'}, format='json')
    assert res_usd.status_code == status.HTTP_201_CREATED

    # Same user cannot create another USD wallet
    res_usd_dup = client_a.post('/api/wallets/', {'currency': 'USD'}, format='json')
    assert res_usd_dup.status_code == status.HTTP_400_BAD_REQUEST

    # List returns both wallets
    res_list = client_a.get('/api/wallets/')
    assert res_list.status_code == status.HTTP_200_OK
    assert res_list.data['count'] == 2


@pytest.mark.django_db
def test_closed_wallet_restrictions(client_a, client_b, user_a, user_b, admin_client):
    from apps.payments.services import PaymentService
    from rest_framework_simplejwt.tokens import RefreshToken

    token_a = str(RefreshToken.for_user(user_a).access_token)
    token_b = str(RefreshToken.for_user(user_b).access_token)

    # Create wallet and close it
    res = client_a.post('/api/wallets/', {'currency': 'INR'}, format='json')
    wallet_id = res.data['id']
    wallet = Wallet.objects.get(id=wallet_id)
    wallet.state = WalletState.CLOSED
    wallet.save()

    # 1. Closed wallet cannot be topped up
    client_a.credentials(HTTP_AUTHORIZATION=f"Bearer {token_a}", HTTP_IDEMPOTENCY_KEY="closed_topup_01")
    res_topup = client_a.post(f'/api/wallets/{wallet_id}/topups/', {
        'amount': '100.00',
        'currency': 'INR'
    }, format='json')
    assert res_topup.status_code == status.HTTP_400_BAD_REQUEST
    assert "closed" in str(res_topup.data).lower()

    # 2. Closed wallet cannot withdraw
    client_a.credentials(HTTP_AUTHORIZATION=f"Bearer {token_a}", HTTP_IDEMPOTENCY_KEY="closed_wdr_01")
    res_wdr = client_a.post(f'/api/wallets/{wallet_id}/withdrawals/', {
        'amount': '50.00',
        'currency': 'INR',
        'destination_bank_account': 'HDFC_001'
    }, format='json')
    assert res_wdr.status_code == status.HTTP_400_BAD_REQUEST
    assert "closed" in str(res_wdr.data).lower()

    # 3. Closed wallet cannot receive transfers
    res_b = client_b.post('/api/wallets/', {'currency': 'INR'}, format='json')
    b_wallet_id = res_b.data['id']
    PaymentService.topup(b_wallet_id, 100000, 'INR', user_b, 'SUCCESS')

    client_b.credentials(HTTP_AUTHORIZATION=f"Bearer {token_b}", HTTP_IDEMPOTENCY_KEY="transfer_to_closed_01")
    res_transfer = client_b.post('/api/transfers/', {
        'source_wallet_id': b_wallet_id,
        'destination_wallet_id': wallet_id,
        'amount': '100.00',
        'currency': 'INR'
    }, format='json')
    assert res_transfer.status_code == status.HTTP_400_BAD_REQUEST
    assert "closed" in str(res_transfer.data).lower()


