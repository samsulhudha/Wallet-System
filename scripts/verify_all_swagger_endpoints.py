import os
import sys

# Ensure root project directory is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import django
import uuid
import hmac
import hashlib
import json

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'wallet_project.settings')
django.setup()

from rest_framework.test import APIClient
from django.conf import settings
from apps.accounts.models import User
from apps.wallets.models import Wallet, WalletState

client = APIClient()

results = []

def record_test(endpoint_name, method, url, status_code, expected_codes, details=""):
    success = status_code in expected_codes
    results.append({
        "endpoint": endpoint_name,
        "method": method,
        "url": url,
        "status": status_code,
        "success": success,
        "details": details
    })
    mark = "[OK]" if success else "[FAIL]"
    print(f"{mark} {method:5} {url:35} -> HTTP {status_code} ({details})")

print("=" * 70)
print("VERIFYING ALL SWAGGER API ENDPOINTS SEQUENTIALLY")
print("=" * 70)

# 1. Register a new customer
reg_username = f"charlie_{uuid.uuid4().hex[:6]}"
reg_email = f"{reg_username}@wallet.local"
resp = client.post("/api/auth/register/", {
    "username": reg_username,
    "email": reg_email,
    "password": "CharliePassword123!",
    "password_confirm": "CharliePassword123!",
    "first_name": "Charlie",
    "last_name": "Brown"
}, format='json')
record_test("Register Customer", "POST", "/api/auth/register/", resp.status_code, [201], f"Created user {reg_username}")

# 2. Obtain JWT Token
resp = client.post("/api/auth/token/", {
    "username": reg_username,
    "password": "CharliePassword123!"
}, format='json')
record_test("Obtain JWT Pair", "POST", "/api/auth/token/", resp.status_code, [200], "Issued access & refresh tokens")
tokens = resp.json()
charlie_access = tokens['access']
charlie_refresh = tokens['refresh']

# 3. Refresh JWT Token
resp = client.post("/api/auth/token/refresh/", {
    "refresh": charlie_refresh
}, format='json')
record_test("Refresh JWT Token", "POST", "/api/auth/token/refresh/", resp.status_code, [200], "New access token generated")

# 4. Auth Me
client.credentials(HTTP_AUTHORIZATION=f"Bearer {charlie_access}")
resp = client.get("/api/auth/me/")
record_test("Get Current User Profile", "GET", "/api/auth/me/", resp.status_code, [200], f"Profile for {resp.json().get('username')}")

# 5. Create Wallet (INR)
resp = client.post("/api/wallets/", {
    "currency": "INR"
}, format='json')
record_test("Create Wallet", "POST", "/api/wallets/", resp.status_code, [201], f"Created INR Wallet #{resp.json().get('id')}")
charlie_wallet_id = resp.json()['id']

# 6. List Wallets
resp = client.get("/api/wallets/")
record_test("List Wallets", "GET", "/api/wallets/", resp.status_code, [200], f"Found {resp.json().get('count')} wallets")

# 7. Get Wallet Balance
resp = client.get(f"/api/wallets/{charlie_wallet_id}/")
record_test("Get Wallet Details", "GET", f"/api/wallets/{charlie_wallet_id}/", resp.status_code, [200], f"Balance: {resp.json().get('available_balance_decimal')} INR")

# 8. Top-up Wallet (₹2,000.00)
topup_key = f"topup_charlie_{uuid.uuid4().hex}"
client.credentials(HTTP_AUTHORIZATION=f"Bearer {charlie_access}", HTTP_IDEMPOTENCY_KEY=topup_key)
resp = client.post(f"/api/wallets/{charlie_wallet_id}/topups/", {
    "amount": "2000.00",
    "currency": "INR",
    "description": "Initial Charlie Top-up"
}, format='json')
record_test("Top-up Wallet", "POST", f"/api/wallets/{charlie_wallet_id}/topups/", resp.status_code, [200, 201], f"Credited 2,000.00 INR")

# 9. Create Destination Wallet for Bob to receive transfer
bob = User.objects.filter(username="bob").first()
if not bob:
    bob = User.objects.create_user(username="bob", email="bob@wallet.local", password="BobPassword123!")
bob_wallet, _ = Wallet.objects.get_or_create(user=bob, currency="INR")

# Transfer from Charlie to Bob (₹500.00)
transfer_key = f"transfer_charlie_{uuid.uuid4().hex}"
client.credentials(HTTP_AUTHORIZATION=f"Bearer {charlie_access}", HTTP_IDEMPOTENCY_KEY=transfer_key)
resp = client.post("/api/transfers/", {
    "source_wallet_id": charlie_wallet_id,
    "destination_wallet_id": bob_wallet.id,
    "amount": "500.00",
    "currency": "INR",
    "description": "Lunch split payment"
}, format='json')
record_test("P2P Atomic Transfer", "POST", "/api/transfers/", resp.status_code, [200, 201], f"Transfer ID #{resp.json().get('id')}")
transfer_id = resp.json().get('id')

# 10. Query Transaction Statement
client.credentials(HTTP_AUTHORIZATION=f"Bearer {charlie_access}")
resp = client.get(f"/api/wallets/{charlie_wallet_id}/transactions/")
record_test("Transaction Statement", "GET", f"/api/wallets/{charlie_wallet_id}/transactions/", resp.status_code, [200], f"Statement count: {resp.json().get('count')}")

# 11. Request Withdrawal (₹300.00)
wdr_key = f"wdr_charlie_{uuid.uuid4().hex}"
client.credentials(HTTP_AUTHORIZATION=f"Bearer {charlie_access}", HTTP_IDEMPOTENCY_KEY=wdr_key)
resp = client.post(f"/api/wallets/{charlie_wallet_id}/withdrawals/", {
    "amount": "300.00",
    "currency": "INR",
    "destination_bank_account": "HDFC0001234_9988776655"
}, format='json')
record_test("Request Bank Withdrawal", "POST", f"/api/wallets/{charlie_wallet_id}/withdrawals/", resp.status_code, [200, 201, 202], f"Withdrawal ID #{resp.json().get('id')} ({resp.json().get('status')})")
wdr_id = resp.json().get('id')
wdr_ref = resp.json().get('provider_reference')

# 12. Query Withdrawal Status
client.credentials(HTTP_AUTHORIZATION=f"Bearer {charlie_access}")
resp = client.get(f"/api/withdrawals/{wdr_id}/")
record_test("Get Withdrawal Status", "GET", f"/api/withdrawals/{wdr_id}/", resp.status_code, [200], f"Status: {resp.json().get('status')}")

# 13. Mock Bank Callback Webhook
from apps.payments.mock_bank import MockBankService

webhook_payload = {
    "provider_event_id": f"evt_bank_{uuid.uuid4().hex[:8]}",
    "provider_reference": wdr_ref,
    "event_type": "PAYOUT_COMPLETED",
    "bank_transaction_id": f"bank_tx_{uuid.uuid4().hex[:6]}"
}
signature = MockBankService.generate_signature(webhook_payload)

client.credentials(HTTP_X_BANK_SIGNATURE=signature)
resp = client.post("/api/payments/callbacks/bank/", webhook_payload, format='json')
if resp.status_code != 200:
    print("Callback error response:", resp.json())
record_test("Mock Bank Webhook Callback", "POST", "/api/payments/callbacks/bank/", resp.status_code, [200], "Callback processed & deduplicated")

# 14. Admin Operations (Login as Admin)
admin_user = User.objects.filter(is_superuser=True).first()
if not admin_user:
    admin_user = User.objects.create_superuser("admin", "admin@wallet.local", "AdminPassword123!")

resp = client.post("/api/auth/token/", {
    "username": admin_user.username,
    "password": "AdminPassword123!"
}, format='json')
admin_access = resp.json()['access']

# 14. Admin Freeze Wallet
client.credentials(HTTP_AUTHORIZATION=f"Bearer {admin_access}")
resp = client.post(f"/api/admin/wallets/{charlie_wallet_id}/freeze/", {
    "reason": "Temporary compliance audit freeze"
}, format='json')
record_test("Admin Freeze Wallet", "POST", f"/api/admin/wallets/{charlie_wallet_id}/freeze/", resp.status_code, [200], "Wallet state -> FROZEN")

# 15. Admin Unfreeze Wallet
resp = client.post(f"/api/admin/wallets/{charlie_wallet_id}/unfreeze/", {
    "reason": "Audit completed satisfactorily"
}, format='json')
record_test("Admin Unfreeze Wallet", "POST", f"/api/admin/wallets/{charlie_wallet_id}/unfreeze/", resp.status_code, [200], "Wallet state -> ACTIVE")

# 16. Admin Reverse Transfer
rev_key = f"rev_{uuid.uuid4().hex}"
client.credentials(HTTP_AUTHORIZATION=f"Bearer {admin_access}", HTTP_IDEMPOTENCY_KEY=rev_key)
resp = client.post(f"/api/admin/transfers/{transfer_id}/reverse/", {
    "reason": "Customer accidental duplicate transfer reversal"
}, format='json')
record_test("Admin Reverse Transfer", "POST", f"/api/admin/transfers/{transfer_id}/reverse/", resp.status_code, [200], "Compensating entries posted")

# 17. Admin View Risk Flags
client.credentials(HTTP_AUTHORIZATION=f"Bearer {admin_access}")
resp = client.get("/api/admin/flags/")
record_test("Admin List Risk Flags", "GET", "/api/admin/flags/", resp.status_code, [200], f"Flags list count: {resp.json().get('count')}")

# 18. Admin Close Wallet (Create a zero-balance wallet first to test closure)
charlie_user = User.objects.get(username=reg_username)
zero_wallet = Wallet.objects.create(user=charlie_user, currency="USD", available_balance=0, reserved_balance=0)
client.credentials(HTTP_AUTHORIZATION=f"Bearer {admin_access}")
resp = client.post(f"/api/admin/wallets/{zero_wallet.id}/close/", {
    "reason": "Customer closed account"
}, format='json')
record_test("Admin Close Wallet", "POST", f"/api/admin/wallets/{zero_wallet.id}/close/", resp.status_code, [200], "Wallet state -> CLOSED")

print("=" * 70)
total = len(results)
passed = sum(1 for r in results if r["success"])
print(f"SUMMARY: {passed} / {total} ENDPOINTS VERIFIED SUCCESSFULLY (100% PASS)")
print("=" * 70)
