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


@pytest.mark.django_db
def test_transfer_idempotency_same_and_different_payload(client_a, wallet_a_inr, wallet_b_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 100000, "INR", user_a)
    idem_key = f"transfer-idem-{uuid.uuid4().hex}"
    headers = {'HTTP_IDEMPOTENCY_KEY': idem_key}

    payload = {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '200.00',
        'currency': 'INR'
    }

    # 1. Initial execution
    res1 = client_a.post('/api/transfers/', payload, format='json', **headers)
    assert res1.status_code == status.HTTP_201_CREATED
    tx_id = res1.data['id']

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 80000

    # 2. Identical retry returns cached result
    res2 = client_a.post('/api/transfers/', payload, format='json', **headers)
    assert res2.status_code == status.HTTP_201_CREATED
    assert res2.data['id'] == tx_id

    # Verify no second debit
    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.available_balance == 80000

    # 3. Same key with different payload returns 409 Conflict
    different_payload = {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '300.00',
        'currency': 'INR'
    }
    res3 = client_a.post('/api/transfers/', different_payload, format='json', **headers)
    assert res3.status_code == status.HTTP_409_CONFLICT
    assert "different request payload" in res3.data['error']['message'].lower()


@pytest.mark.django_db
def test_transfer_zero_negative_and_invalid_amounts(client_a, wallet_a_inr, wallet_b_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 100000, "INR", user_a)

    # 1. Zero amount
    res_zero = client_a.post('/api/transfers/', {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '0.00',
        'currency': 'INR'
    }, format='json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert res_zero.status_code == status.HTTP_400_BAD_REQUEST

    # 2. Negative amount
    res_neg = client_a.post('/api/transfers/', {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '-50.00',
        'currency': 'INR'
    }, format='json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert res_neg.status_code == status.HTTP_400_BAD_REQUEST

    # 3. Excessive precision (3 decimal places)
    res_prec = client_a.post('/api/transfers/', {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': wallet_b_inr.id,
        'amount': '50.123',
        'currency': 'INR'
    }, format='json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert res_prec.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_transfer_invalid_recipient_wallet(client_a, wallet_a_inr, user_a):
    PaymentService.topup(wallet_a_inr.id, 100000, "INR", user_a)

    res = client_a.post('/api/transfers/', {
        'source_wallet_id': wallet_a_inr.id,
        'destination_wallet_id': 999999,
        'amount': '50.00',
        'currency': 'INR'
    }, format='json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert res.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND]


