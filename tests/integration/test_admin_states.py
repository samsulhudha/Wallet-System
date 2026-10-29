import pytest
from rest_framework import status
from apps.wallets.models import Wallet, WalletState
from apps.audit.models import WalletAuditLog, AdminAuditLog
from apps.payments.services import PaymentService


@pytest.mark.django_db
def test_admin_wallet_state_lifecycle(admin_client, client_a, wallet_a_inr, user_a):
    # 1. Non-admin cannot freeze
    res_unauth = client_a.post(f'/api/admin/wallets/{wallet_a_inr.id}/freeze/', {'reason': 'Test'}, format='json')
    assert res_unauth.status_code == status.HTTP_403_FORBIDDEN

    # 2. Admin freezes wallet
    res_freeze = admin_client.post(
        f'/api/admin/wallets/{wallet_a_inr.id}/freeze/',
        {'reason': 'Suspicious chargeback investigation'},
        format='json'
    )
    assert res_freeze.status_code == status.HTTP_200_OK
    assert res_freeze.data['state'] == WalletState.FROZEN

    # Verify audit log recorded
    assert WalletAuditLog.objects.filter(wallet=wallet_a_inr, new_state=WalletState.FROZEN).exists()

    # 3. Admin unfreezes wallet
    res_unfreeze = admin_client.post(
        f'/api/admin/wallets/{wallet_a_inr.id}/unfreeze/',
        {'reason': 'Investigation cleared'},
        format='json'
    )
    assert res_unfreeze.status_code == status.HTTP_200_OK
    assert res_unfreeze.data['state'] == WalletState.ACTIVE

    # 4. Attempt to close wallet with non-zero balance fails
    PaymentService.topup(wallet_a_inr.id, 10000, "INR", user_a)
    res_close_fail = admin_client.post(
        f'/api/admin/wallets/{wallet_a_inr.id}/close/',
        {'reason': 'Account shutdown'},
        format='json'
    )
    assert res_close_fail.status_code == status.HTTP_400_BAD_REQUEST
    assert "must be exactly zero" in res_close_fail.data['error']['message'].lower()

