import pytest
from django.core.exceptions import PermissionDenied
from apps.ledger.models import (
    LedgerAccount,
    LedgerAccountType,
    LedgerTransaction,
    LedgerEntry,
    TransactionType,
    EntryDirection,
)
from apps.ledger.services import LedgerService, LedgerUnbalancedError
from apps.wallets.models import Wallet, WalletState


@pytest.mark.django_db
def test_balanced_ledger_transaction(wallet_a_inr):
    acc1 = LedgerService.get_or_create_wallet_account(wallet_a_inr, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)
    acc2 = LedgerService.get_or_create_system_account(LedgerAccountType.PAYMENT_GATEWAY_CLEARING, "INR")

    entries = [
        {'account': acc2, 'direction': EntryDirection.DEBIT, 'amount': 5000},
        {'account': acc1, 'direction': EntryDirection.CREDIT, 'amount': 5000},
    ]

    tx = LedgerService.record_transaction(
        reference_id="test_ref_1",
        transaction_type=TransactionType.TOPUP,
        currency="INR",
        entries=entries,
        description="Test balanced transaction"
    )

    assert tx.id is not None
    assert tx.entries.count() == 2

    # Check database records
    debits = sum(e.amount for e in tx.entries.filter(direction=EntryDirection.DEBIT))
    credits = sum(e.amount for e in tx.entries.filter(direction=EntryDirection.CREDIT))
    assert debits == credits == 5000


@pytest.mark.django_db
def test_unbalanced_ledger_transaction_rejected(wallet_a_inr):
    acc1 = LedgerService.get_or_create_wallet_account(wallet_a_inr, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)
    acc2 = LedgerService.get_or_create_system_account(LedgerAccountType.PAYMENT_GATEWAY_CLEARING, "INR")

    # Unbalanced: 5000 vs 4000
    entries = [
        {'account': acc2, 'direction': EntryDirection.DEBIT, 'amount': 5000},
        {'account': acc1, 'direction': EntryDirection.CREDIT, 'amount': 4000},
    ]

    with pytest.raises(LedgerUnbalancedError, match="does not balance"):
        LedgerService.record_transaction(
            reference_id="test_ref_unbalanced",
            transaction_type=TransactionType.TOPUP,
            currency="INR",
            entries=entries
        )


@pytest.mark.django_db
def test_currency_mismatch_in_ledger_entries_rejected(wallet_a_inr, wallet_a_usd):
    acc_inr = LedgerService.get_or_create_wallet_account(wallet_a_inr, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)
    acc_usd = LedgerService.get_or_create_wallet_account(wallet_a_usd, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)

    entries = [
        {'account': acc_inr, 'direction': EntryDirection.DEBIT, 'amount': 1000},
        {'account': acc_usd, 'direction': EntryDirection.CREDIT, 'amount': 1000},
    ]

    with pytest.raises(LedgerUnbalancedError, match="does not match transaction currency"):
        LedgerService.record_transaction(
            reference_id="test_ref_curr_mismatch",
            transaction_type=TransactionType.TRANSFER,
            currency="INR",
            entries=entries
        )


@pytest.mark.django_db
def test_ledger_entry_immutability(wallet_a_inr):
    acc = LedgerService.get_or_create_wallet_account(wallet_a_inr, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)
    tx = LedgerTransaction.objects.create(
        reference_id="ref_immutability",
        transaction_type=TransactionType.TOPUP,
        currency="INR"
    )
    entry = LedgerEntry.objects.create(
        transaction=tx,
        account=acc,
        direction=EntryDirection.CREDIT,
        amount=1000
    )

    # Attempt modification
    entry.amount = 2000
    with pytest.raises(PermissionDenied, match="immutable and cannot be updated"):
        entry.save()

    # Attempt deletion
    with pytest.raises(PermissionDenied, match="immutable and cannot be deleted"):
        entry.delete()
