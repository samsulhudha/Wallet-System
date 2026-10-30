import pytest
import hmac
import hashlib
import json
from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from apps.accounts.models import User
from apps.wallets.models import Wallet, WalletState
from apps.payments.services import PaymentService


@pytest.mark.django_db
class TestSecurityAndAuthentication:
    """
    Comprehensive security test suite validating:
    - Authentication enforcement & token validity
    - Object-level authorization & tenant isolation
    - Privilege escalation protection
    - Webhook HMAC signature verification
    - Input sanitization & injection resistance
    """

    def test_missing_and_invalid_authentication(self):
        client = APIClient()

        # 1. Missing Authorization header
        resp = client.get("/api/wallets/")
        assert resp.status_code == 401

        resp = client.post("/api/transfers/", {})
        assert resp.status_code == 401

        # 2. Malformed token
        client.credentials(HTTP_AUTHORIZATION="Bearer garbage.token.here")
        resp = client.get("/api/wallets/")
        assert resp.status_code == 401

        # 3. Non-bearer prefix
        client.credentials(HTTP_AUTHORIZATION="Basic dXNlcjpwYXNz")
        resp = client.get("/api/wallets/")
        assert resp.status_code == 401

    def test_expired_jwt_token_rejection(self):
        client = APIClient()
        user = User.objects.create_user(username="expired_user", email="expired@wallet.local", password="Password123!")

        # Create token and manually set expired timestamp
        refresh = RefreshToken.for_user(user)
        access = refresh.access_token
        access.set_exp(lifetime=-timedelta(minutes=10))

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(access)}")
        resp = client.get("/api/wallets/")
        assert resp.status_code == 401
        assert resp.data['error']['code'] == 'AUTHENTICATION_FAILED'
        assert "token not valid" in resp.data['error']['message'].lower()

    def test_cross_user_wallet_isolation(self):
        """Verify strict object-level authorization: User A cannot read or modify User B's resources."""
        user_a = User.objects.create_user(username="alice_sec", email="alice_sec@wallet.local", password="Password123!")
        user_b = User.objects.create_user(username="bob_sec", email="bob_sec@wallet.local", password="Password123!")

        wallet_a = Wallet.objects.create(user=user_a, currency="INR")
        wallet_b = Wallet.objects.create(user=user_b, currency="INR")

        PaymentService.topup(wallet_b.id, 50000, "INR", user_b, "SUCCESS")

        client = APIClient()
        token_a = str(RefreshToken.for_user(user_a).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_a}")

        # Alice cannot view Bob's wallet details
        resp = client.get(f"/api/wallets/{wallet_b.id}/")
        assert resp.status_code in [403, 404]

        # Alice cannot view Bob's transaction statement
        resp = client.get(f"/api/wallets/{wallet_b.id}/transactions/")
        assert resp.status_code in [403, 404]

        # Alice cannot initiate a withdrawal from Bob's wallet
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_a}", HTTP_IDEMPOTENCY_KEY="sec_wdr_001")
        resp = client.post(f"/api/wallets/{wallet_b.id}/withdrawals/", {
            "amount": "100.00",
            "currency": "INR",
            "destination_bank_account": "HDFC_12345"
        }, format='json')
        assert resp.status_code in [403, 404]

        # Alice cannot initiate a transfer where Bob is the source wallet
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_a}", HTTP_IDEMPOTENCY_KEY="sec_transfer_001")
        resp = client.post("/api/transfers/", {
            "source_wallet_id": wallet_b.id,
            "destination_wallet_id": wallet_a.id,
            "amount": "100.00",
            "currency": "INR"
        }, format='json')
        assert resp.status_code == 403

    def test_privilege_escalation_protection(self):
        """Verify regular authenticated users cannot access any admin endpoint."""
        customer = User.objects.create_user(username="regular_user", email="regular@wallet.local", password="Password123!")
        target_wallet = Wallet.objects.create(user=customer, currency="INR")

        client = APIClient()
        token = str(RefreshToken.for_user(customer).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # 1. Admin Freeze Wallet
        resp = client.post(f"/api/admin/wallets/{target_wallet.id}/freeze/", {"reason": "Hacking attempt"}, format='json')
        assert resp.status_code == 403

        # 2. Admin Unfreeze Wallet
        resp = client.post(f"/api/admin/wallets/{target_wallet.id}/unfreeze/", {"reason": "Hacking attempt"}, format='json')
        assert resp.status_code == 403

        # 3. Admin Close Wallet
        resp = client.post(f"/api/admin/wallets/{target_wallet.id}/close/", {"reason": "Hacking attempt"}, format='json')
        assert resp.status_code == 403

        # 4. Admin Reverse Transfer
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_IDEMPOTENCY_KEY="sec_rev_001")
        resp = client.post("/api/admin/transfers/1/reverse/", {"reason": "Hacking attempt"}, format='json')
        assert resp.status_code == 403

        # 5. Admin Risk Flags
        resp = client.get("/api/admin/flags/")
        assert resp.status_code == 403

    def test_bank_callback_hmac_authentication(self):
        """Verify mock bank webhook rejects forged, missing, or altered signatures."""
        client = APIClient()
        payload = {
            "provider_event_id": "sec_evt_001",
            "provider_reference": "wdr_sec_nonexistent",
            "event_type": "PAYOUT_COMPLETED"
        }

        # 1. Missing signature
        resp = client.post("/api/payments/callbacks/bank/", payload, format='json')
        assert resp.status_code == 400
        assert "Invalid signature" in str(resp.data)

        # 2. Forged signature
        client.credentials(HTTP_X_BANK_SIGNATURE="deadbeef1234567890abcdef")
        resp = client.post("/api/payments/callbacks/bank/", payload, format='json')
        assert resp.status_code == 400
        assert "Invalid signature" in str(resp.data)

        # 3. Signature mismatch when payload is tampered
        secret = b"super-secret-bank-callback-key-2026"
        valid_raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        valid_sig = hmac.new(secret, valid_raw.encode('utf-8'), hashlib.sha256).hexdigest()

        tampered_payload = payload.copy()
        tampered_payload["event_type"] = "PAYOUT_REJECTED"

        client.credentials(HTTP_X_BANK_SIGNATURE=valid_sig)
        resp = client.post("/api/payments/callbacks/bank/", tampered_payload, format='json')
        assert resp.status_code == 400
        assert "Invalid signature" in str(resp.data)

    def test_sql_injection_and_input_sanitization(self):
        """Verify SQL injection strings are safely handled by Django ORM / serializers."""
        user = User.objects.create_user(username="sqli_user", email="sqli@wallet.local", password="Password123!")
        wallet = Wallet.objects.create(user=user, currency="INR")

        client = APIClient()
        token = str(RefreshToken.for_user(user).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # SQL injection in statement date query parameters
        sqli_param = "2026-01-01' OR '1'='1'; DROP TABLE wallets; --"
        resp = client.get(f"/api/wallets/{wallet.id}/transactions/?start_date={sqli_param}")
        # Should gracefully return 400 (invalid datetime format) rather than execute SQL
        assert resp.status_code in [200, 400]
        assert Wallet.objects.filter(id=wallet.id).exists()

        # SQL injection in amount field
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_IDEMPOTENCY_KEY="sqli_topup")
        resp = client.post(f"/api/wallets/{wallet.id}/topups/", {
            "amount": "100.00; DROP TABLE wallets; --",
            "currency": "INR"
        }, format='json')
        assert resp.status_code == 400
        assert Wallet.objects.filter(id=wallet.id).exists()
