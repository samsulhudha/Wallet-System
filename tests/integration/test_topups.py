import pytest
import uuid
from rest_framework import status
from apps.wallets.models import Wallet
from apps.ledger.models import LedgerEntry, EntryDirection


@pytest.mark.django_db
def test_topup_success_and_idempotency(client_a, wallet_a_inr):
    idem_key = str(uuid.uuid4())
    headers = {'HTTP_IDEMPOTENCY_KEY': idem_key}

    # 1. First top-up: 500.00 INR
    res1 = client_a.post(
        f'/api/wallets/{wallet_a_inr.id}/topups/',
        {'amount': '500.00', 'currency': 'INR'},
        format='json',
        **headers
    )
    assert res1.status_code == status.HTTP_201_CREATED
    assert res1.data['status'] == 'COMPLETED'
    assert res1.data['amount'] == 50000

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 50000

    # Verify ledger entries created
    entries = LedgerEntry.objects.filter(account__wallet=wallet_a_inr)
    assert entries.count() == 1
    assert entries.first().direction == EntryDirection.CREDIT
    assert entries.first().amount == 50000

    # 2. Repeated request with identical key returns original result and DOES NOT credit again
    res2 = client_a.post(
        f'/api/wallets/{wallet_a_inr.id}/topups/',
        {'amount': '500.00', 'currency': 'INR'},
        format='json',
        **headers
    )
    assert res2.status_code == status.HTTP_201_CREATED
    assert res2.data['id'] == res1.data['id']

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 50000  # Still 50,000, not 100,000!


@pytest.mark.django_db
def test_topup_failure_leaves_balance_untouched(client_a, wallet_a_inr):
    idem_key = str(uuid.uuid4())
    headers = {'HTTP_IDEMPOTENCY_KEY': idem_key}

    res = client_a.post(
        f'/api/wallets/{wallet_a_inr.id}/topups/',
        {'amount': '250.00', 'currency': 'INR', 'mock_provider_outcome': 'FAILURE'},
        format='json',
        **headers
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert res.data['status'] == 'FAILED'

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 0
    assert LedgerEntry.objects.filter(account__wallet=wallet_a_inr).count() == 0


@pytest.mark.django_db
def test_topup_zero_negative_and_invalid_amounts(client_a, wallet_a_inr):
    # 1. Zero amount
    res_zero = client_a.post(
        f'/api/wallets/{wallet_a_inr.id}/topups/',
        {'amount': '0.00', 'currency': 'INR'},
        format='json',
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
    )
    assert res_zero.status_code == status.HTTP_400_BAD_REQUEST

    # 2. Negative amount
    res_neg = client_a.post(
        f'/api/wallets/{wallet_a_inr.id}/topups/',
        {'amount': '-100.00', 'currency': 'INR'},
        format='json',
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
    )
    assert res_neg.status_code == status.HTTP_400_BAD_REQUEST

    # 3. Excessive precision
    res_prec = client_a.post(
        f'/api/wallets/{wallet_a_inr.id}/topups/',
        {'amount': '10.555', 'currency': 'INR'},
        format='json',
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
    )
    assert res_prec.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_topup_idempotency_different_payload_conflict(client_a, wallet_a_inr):
    idem_key = f"topup-conflict-{uuid.uuid4().hex}"
    headers = {'HTTP_IDEMPOTENCY_KEY': idem_key}

    # First request: 500 INR
    res1 = client_a.post(
        f'/api/wallets/{wallet_a_inr.id}/topups/',
        {'amount': '500.00', 'currency': 'INR'},
        format='json',
        **headers
    )
    assert res1.status_code == status.HTTP_201_CREATED

    # Second request with same key but 600 INR
    res2 = client_a.post(
        f'/api/wallets/{wallet_a_inr.id}/topups/',
        {'amount': '600.00', 'currency': 'INR'},
        format='json',
        **headers
    )
    assert res2.status_code == status.HTTP_409_CONFLICT
    assert "different request payload" in res2.data['error']['message'].lower()


