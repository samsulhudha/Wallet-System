from typing import List, Dict, Any
from django.db import transaction
from django.db.models import Sum
from apps.wallets.models import Wallet
from .models import (
    LedgerAccount,
    LedgerAccountType,
    LedgerTransaction,
    LedgerEntry,
    TransactionType,
    EntryDirection,
)
from apps.common.exceptions import DomainError


class LedgerUnbalancedError(DomainError):
    default_code = "LEDGER_UNBALANCED"


class LedgerService:
    """
    Core double-entry bookkeeping service.
    Enforces atomic posting of balancing entries where Sum(Debits) == Sum(Credits).
    """

    @classmethod
    def get_or_create_wallet_account(cls, wallet: Wallet, account_type: str) -> LedgerAccount:
        """Retrieves or creates a ledger account attached to a specific wallet."""
        account, _ = LedgerAccount.objects.get_or_create(
            wallet=wallet,
            account_type=account_type,
            currency=wallet.currency,
            defaults={
                'name': f"Wallet #{wallet.id} {account_type} ({wallet.currency})"
            }
        )
        return account

    @classmethod
    def get_or_create_system_account(cls, account_type: str, currency: str) -> LedgerAccount:
        """Retrieves or creates a system-wide clearing or adjustment account."""
        account, _ = LedgerAccount.objects.get_or_create(
            wallet=None,
            account_type=account_type,
            currency=currency,
            defaults={
                'name': f"System {account_type} ({currency})"
            }
        )
        return account

    @classmethod
    @transaction.atomic
    def record_transaction(
        cls,
        reference_id: str,
        transaction_type: str,
        currency: str,
        entries: List[Dict[str, Any]],
        description: str = ""
    ) -> LedgerTransaction:
        """
        Atomically records a double-entry transaction and all its entries.
        Enforces that Sum(Debits) == Sum(Credits) and all amounts > 0.
        """
        if not entries:
            raise LedgerUnbalancedError("Cannot post an empty ledger transaction.")

        total_debits = 0
        total_credits = 0

        for idx, entry in enumerate(entries):
            amount = entry.get('amount', 0)
            direction = entry.get('direction')
            account = entry.get('account')

            if not isinstance(amount, int) or amount <= 0:
                raise LedgerUnbalancedError(f"Entry {idx} amount must be a positive integer, got: {amount}")

            if account.currency != currency:
                raise LedgerUnbalancedError(
                    f"Entry {idx} account currency '{account.currency}' does not match transaction currency '{currency}'"
                )

            if direction == EntryDirection.DEBIT:
                total_debits += amount
            elif direction == EntryDirection.CREDIT:
                total_credits += amount
            else:
                raise LedgerUnbalancedError(f"Entry {idx} invalid direction: {direction}")

        if total_debits != total_credits:
            raise LedgerUnbalancedError(
                f"Ledger transaction does not balance! Total Debits: {total_debits}, Total Credits: {total_credits} (Diff: {total_debits - total_credits})"
            )

        tx = LedgerTransaction.objects.create(
            reference_id=reference_id,
            transaction_type=transaction_type,
            currency=currency,
            description=description,
        )

        entries_to_create = [
            LedgerEntry(
                transaction=tx,
                account=e['account'],
                direction=e['direction'],
                amount=e['amount']
            )
            for e in entries
        ]
        LedgerEntry.objects.bulk_create(entries_to_create)

        return tx

    @classmethod
    def record_topup(cls, wallet: Wallet, amount: int, reference_id: str, description: str = "") -> LedgerTransaction:
        """
        Top-up accounting:
        - DEBIT: Payment Gateway Clearing (Asset +)
        - CREDIT: Customer Wallet Available (Liability +)
        """
        gateway_acc = cls.get_or_create_system_account(LedgerAccountType.PAYMENT_GATEWAY_CLEARING, wallet.currency)
        wallet_avail_acc = cls.get_or_create_wallet_account(wallet, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)

        entries = [
            {'account': gateway_acc, 'direction': EntryDirection.DEBIT, 'amount': amount},
            {'account': wallet_avail_acc, 'direction': EntryDirection.CREDIT, 'amount': amount},
        ]
        return cls.record_transaction(
            reference_id=reference_id,
            transaction_type=TransactionType.TOPUP,
            currency=wallet.currency,
            entries=entries,
            description=description or f"Top-up of {amount} minor units for Wallet #{wallet.id}"
        )

    @classmethod
    def record_transfer(
        cls,
        source_wallet: Wallet,
        dest_wallet: Wallet,
        amount: int,
        reference_id: str,
        description: str = ""
    ) -> LedgerTransaction:
        """
        P2P Transfer accounting:
        - DEBIT: Source Wallet Available (Liability -)
        - CREDIT: Destination Wallet Available (Liability +)
        """
        src_acc = cls.get_or_create_wallet_account(source_wallet, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)
        dst_acc = cls.get_or_create_wallet_account(dest_wallet, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)

        entries = [
            {'account': src_acc, 'direction': EntryDirection.DEBIT, 'amount': amount},
            {'account': dst_acc, 'direction': EntryDirection.CREDIT, 'amount': amount},
        ]
        return cls.record_transaction(
            reference_id=reference_id,
            transaction_type=TransactionType.TRANSFER,
            currency=source_wallet.currency,
            entries=entries,
            description=description or f"Transfer #{reference_id} from Wallet #{source_wallet.id} to #{dest_wallet.id}"
        )

    @classmethod
    def record_withdrawal_reserve(
        cls,
        wallet: Wallet,
        amount: int,
        reference_id: str,
        description: str = ""
    ) -> LedgerTransaction:
        """
        Withdrawal Fund Reservation accounting:
        - DEBIT: Customer Wallet Available (Liability -)
        - CREDIT: Customer Wallet Reserved (Liability +)
        """
        avail_acc = cls.get_or_create_wallet_account(wallet, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)
        res_acc = cls.get_or_create_wallet_account(wallet, LedgerAccountType.CUSTOMER_WALLET_RESERVED)

        entries = [
            {'account': avail_acc, 'direction': EntryDirection.DEBIT, 'amount': amount},
            {'account': res_acc, 'direction': EntryDirection.CREDIT, 'amount': amount},
        ]
        return cls.record_transaction(
            reference_id=reference_id,
            transaction_type=TransactionType.WITHDRAWAL_RESERVE,
            currency=wallet.currency,
            entries=entries,
            description=description or f"Withdrawal reservation #{reference_id} for Wallet #{wallet.id}"
        )

    @classmethod
    def record_withdrawal_settle(
        cls,
        wallet: Wallet,
        amount: int,
        reference_id: str,
        description: str = ""
    ) -> LedgerTransaction:
        """
        Withdrawal Settlement accounting (Bank confirmed payout):
        - DEBIT: Customer Wallet Reserved (Liability -)
        - CREDIT: Bank Settlement Clearing (Asset -)
        """
        res_acc = cls.get_or_create_wallet_account(wallet, LedgerAccountType.CUSTOMER_WALLET_RESERVED)
        bank_acc = cls.get_or_create_system_account(LedgerAccountType.BANK_SETTLEMENT_CLEARING, wallet.currency)

        entries = [
            {'account': res_acc, 'direction': EntryDirection.DEBIT, 'amount': amount},
            {'account': bank_acc, 'direction': EntryDirection.CREDIT, 'amount': amount},
        ]
        return cls.record_transaction(
            reference_id=reference_id,
            transaction_type=TransactionType.WITHDRAWAL_SETTLE,
            currency=wallet.currency,
            entries=entries,
            description=description or f"Withdrawal settlement #{reference_id} for Wallet #{wallet.id}"
        )

    @classmethod
    def record_withdrawal_refund(
        cls,
        wallet: Wallet,
        amount: int,
        reference_id: str,
        description: str = ""
    ) -> LedgerTransaction:
        """
        Withdrawal Refund compensating entry (Bank rejected payout):
        - DEBIT: Customer Wallet Reserved (Liability -)
        - CREDIT: Customer Wallet Available (Liability +)
        """
        res_acc = cls.get_or_create_wallet_account(wallet, LedgerAccountType.CUSTOMER_WALLET_RESERVED)
        avail_acc = cls.get_or_create_wallet_account(wallet, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)

        entries = [
            {'account': res_acc, 'direction': EntryDirection.DEBIT, 'amount': amount},
            {'account': avail_acc, 'direction': EntryDirection.CREDIT, 'amount': amount},
        ]
        return cls.record_transaction(
            reference_id=reference_id,
            transaction_type=TransactionType.WITHDRAWAL_REFUND,
            currency=wallet.currency,
            entries=entries,
            description=description or f"Withdrawal refund #{reference_id} for Wallet #{wallet.id}"
        )

    @classmethod
    def record_transfer_reversal(
        cls,
        source_wallet: Wallet,
        dest_wallet: Wallet,
        amount: int,
        reference_id: str,
        description: str = ""
    ) -> LedgerTransaction:
        """
        Transfer Reversal compensating entry:
        - DEBIT: Destination Wallet Available (Liability -)
        - CREDIT: Source Wallet Available (Liability +)
        """
        dst_acc = cls.get_or_create_wallet_account(dest_wallet, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)
        src_acc = cls.get_or_create_wallet_account(source_wallet, LedgerAccountType.CUSTOMER_WALLET_AVAILABLE)

        entries = [
            {'account': dst_acc, 'direction': EntryDirection.DEBIT, 'amount': amount},
            {'account': src_acc, 'direction': EntryDirection.CREDIT, 'amount': amount},
        ]
        return cls.record_transaction(
            reference_id=reference_id,
            transaction_type=TransactionType.TRANSFER_REVERSAL,
            currency=source_wallet.currency,
            entries=entries,
            description=description or f"Transfer reversal #{reference_id} from #{dest_wallet.id} back to #{source_wallet.id}"
        )
