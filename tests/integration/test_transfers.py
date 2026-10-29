import pytest
import uuid
from rest_framework import status
from apps.wallets.models import Wallet, WalletState
from apps.ledger.models import LedgerEntry, EntryDirection
from apps.payments.services import PaymentService


@pytest.mark.django_db
def test_successful_transfer(client_a, wallet_a_inr, wallet_b_inr, user_a):
    # Fund wallet A with 1,000.00 INR (100,000 minor units)
    PaymentService.topup(wallet_a_inr.id, 100000, "INR", user_a)

    idem_key = str(uuid.uuid4())
    headers = {'HTTP_IDEMPOTENCY_KEY': idem_key}

    # Transfer 400.00 INR from A to B
    payload = {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '400.00',
        'currency': 'INR',
        'description': 'Rent split'
    }

    res = client_a.post('/api/transfers/', payload, format='json', **headers)
    assert res.status_code == status.HTTP_201_CREATED
    assert res.data['amount'] == 40000

    wallet_a_inr.refresh_from_db()
    wallet_b_inr.refresh_from_db()

    assert wallet_a_inr.available_balance == 60000
    assert wallet_b_inr.available_balance == 40000

    # Check ledger entries: A debited 40000, B credited 40000
    src_entry = LedgerEntry.objects.filter(account__wallet=wallet_a_inr, transaction__transaction_type='TRANSFER').first()
    dst_entry = LedgerEntry.objects.filter(account__wallet=wallet_b_inr, transaction__transaction_type='TRANSFER').first()

    assert src_entry.direction == EntryDirection.DEBIT
    assert src_entry.amount == 40000
    assert dst_entry.direction == EntryDirection.CREDIT
    assert dst_entry.amount == 40000


@pytest.mark.django_db
def test_transfer_insufficient_funds_rejected(client_a, wallet_a_inr, wallet_b_inr):
    headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    payload = {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '100.00',
        'currency': 'INR'
    }

    res = client_a.post('/api/transfers/', payload, format='json', **headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "insufficient funds" in res.data['error']['message'].lower()


@pytest.mark.django_db
def test_transfer_currency_mismatch_rejected(client_a, wallet_a_inr, wallet_a_usd, user_a):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    payload = {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_a_usd.id,
        'amount': '100.00',
        'currency': 'INR'
    }

    res = client_a.post('/api/transfers/', payload, format='json', **headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "mismatch" in res.data['error']['message'].lower()


@pytest.mark.django_db
def test_self_transfer_rejected(client_a, wallet_a_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    payload = {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_a_inr.id,
        'amount': '50.00',
        'currency': 'INR'
    }

    res = client_a.post('/api/transfers/', payload, format='json', **headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "same wallet" in str(res.data)


@pytest.mark.django_db
def test_frozen_source_wallet_cannot_transfer(client_a, wallet_a_inr, wallet_b_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 50000, "INR", user_a)
    wallet_a_inr.state = WalletState.FROZEN
    wallet_a_inr.save()

    headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
    payload = {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '50.00',
        'currency': 'INR'
    }

    res = client_a.post('/api/transfers/', payload, format='json', **headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "frozen" in res.data['error']['message'].lower()

