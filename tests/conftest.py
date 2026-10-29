import pytest
import uuid
from decimal import Decimal
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from apps.accounts.models import User
from apps.wallets.models import Wallet, WalletState
from apps.payments.services import PaymentService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user_a(db):
    return User.objects.create_user(
        username="user_alice",
        email="alice@example.com",
        password="TestPassword123!"
    )


@pytest.fixture
def user_b(db):
    return User.objects.create_user(
        username="user_bob",
        email="bob@example.com",
        password="TestPassword123!"
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        username="admin_super",
        email="admin@example.com",
        password="AdminPassword123!"
    )


def get_authenticated_client(user):
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(refresh.access_token)}')
    return client


@pytest.fixture
def client_a(user_a):
    return get_authenticated_client(user_a)


@pytest.fixture
def client_b(user_b):
    return get_authenticated_client(user_b)


@pytest.fixture
def admin_client(admin_user):
    return get_authenticated_client(admin_user)


@pytest.fixture
def wallet_a_inr(user_a):
    return Wallet.objects.create(
        user=user_a,
        currency="INR",
        available_balance=0,
        reserved_balance=0,
        state=WalletState.ACTIVE
    )


@pytest.fixture
def wallet_b_inr(user_b):
    return Wallet.objects.create(
        user=user_b,
        currency="INR",
        available_balance=0,
        reserved_balance=0,
        state=WalletState.ACTIVE
    )


@pytest.fixture
def wallet_a_usd(user_a):
    return Wallet.objects.create(
        user=user_a,
        currency="USD",
        available_balance=0,
        reserved_balance=0,
        state=WalletState.ACTIVE
    )


@pytest.fixture
def make_idempotency_key():
    return lambda: str(uuid.uuid4())

