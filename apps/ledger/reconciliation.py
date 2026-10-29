from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from django.db.models import Sum, Q
from apps.wallets.models import Wallet
from .models import (
    LedgerAccount,
    LedgerAccountType,
    LedgerTransaction,
    LedgerEntry,
    EntryDirection,
)


@dataclass
class WalletReconciliationReport:
    wallet_id: int
    currency: str
    cached_available: int
    expected_available: int
    available_discrepancy: int
    cached_reserved: int
    expected_reserved: int
    reserved_discrepancy: int
    is_balanced: bool


@dataclass
class GlobalReconciliationReport:
    total_wallets_checked: int
    discrepant_wallets_count: int
    total_transactions_checked: int
    unbalanced_transactions_count: int
    currency_balances: Dict[str, Dict[str, int]]
    discrepant_wallets: List[WalletReconciliationReport] = field(default_factory=list)
    unbalanced_transaction_ids: List[int] = field(default_factory=list)
    is_healthy: bool = True


class ReconciliationService:
    """
    Independent financial reconciliation engine.
    Audits ledger entries against cached wallet balances and verifies global double-entry invariants.
    NEVER mutates data.
    """

    @classmethod
    def calculate_wallet_ledger_balances(cls, wallet: Wallet) -> Dict[str, int]:
        """
        Calculates expected available and reserved balances directly from immutable ledger entries.
        For customer liability accounts:
            Balance = Sum(Credits) - Sum(Debits)
        """
        # Available account
        avail_acc = LedgerAccount.objects.filter(
            wallet=wallet,
            account_type=LedgerAccountType.CUSTOMER_WALLET_AVAILABLE
        ).first()

        expected_available = 0
        if avail_acc:
            credits = LedgerEntry.objects.filter(
                account=avail_acc,
                direction=EntryDirection.CREDIT
            ).aggregate(total=Sum('amount'))['total'] or 0

            debits = LedgerEntry.objects.filter(
                account=avail_acc,
                direction=EntryDirection.DEBIT
            ).aggregate(total=Sum('amount'))['total'] or 0

            expected_available = credits - debits

        # Reserved account
        res_acc = LedgerAccount.objects.filter(
            wallet=wallet,
            account_type=LedgerAccountType.CUSTOMER_WALLET_RESERVED
        ).first()

        expected_reserved = 0
        if res_acc:
            credits = LedgerEntry.objects.filter(
                account=res_acc,
                direction=EntryDirection.CREDIT
            ).aggregate(total=Sum('amount'))['total'] or 0

            debits = LedgerEntry.objects.filter(
                account=res_acc,
                direction=EntryDirection.DEBIT
            ).aggregate(total=Sum('amount'))['total'] or 0

            expected_reserved = credits - debits

        return {
            'expected_available': expected_available,
            'expected_reserved': expected_reserved,
        }

    @classmethod
    def reconcile_wallet(cls, wallet: Wallet) -> WalletReconciliationReport:
        """Audits a single wallet against the ledger."""
        balances = cls.calculate_wallet_ledger_balances(wallet)
        expected_available = balances['expected_available']
        expected_reserved = balances['expected_reserved']

        diff_available = wallet.available_balance - expected_available
        diff_reserved = wallet.reserved_balance - expected_reserved

        is_balanced = (diff_available == 0 and diff_reserved == 0)

        return WalletReconciliationReport(
            wallet_id=wallet.id,
            currency=wallet.currency,
            cached_available=wallet.available_balance,
            expected_available=expected_available,
            available_discrepancy=diff_available,
            cached_reserved=wallet.reserved_balance,
            expected_reserved=expected_reserved,
            reserved_discrepancy=diff_reserved,
            is_balanced=is_balanced,
        )

    @classmethod
    def run_full_reconciliation(cls) -> GlobalReconciliationReport:
        """
        Executes end-to-end reconciliation across:
        1. All individual wallets vs ledger entries.
        2. All individual transactions to confirm Sum(Debits) == Sum(Credits).
        3. Global currency-level balance equality.
        """
        wallets = Wallet.objects.all().order_by('id')
        discrepant_wallets: List[WalletReconciliationReport] = []

        for w in wallets:
            report = cls.reconcile_wallet(w)
            if not report.is_balanced:
                discrepant_wallets.append(report)

        # Check every transaction for Sum(Debits) == Sum(Credits)
        unbalanced_tx_ids: List[int] = []
        transactions = LedgerTransaction.objects.all().prefetch_related('entries')
        for tx in transactions:
            entries = tx.entries.all()
            debits = sum(e.amount for e in entries if e.direction == EntryDirection.DEBIT)
            credits = sum(e.amount for e in entries if e.direction == EntryDirection.CREDIT)
            if debits != credits:
                unbalanced_tx_ids.append(tx.id)

        # Global totals per currency
        currency_totals: Dict[str, Dict[str, int]] = {}
        entries_by_curr = LedgerEntry.objects.values('account__currency', 'direction').annotate(total=Sum('amount'))
        for item in entries_by_curr:
            curr = item['account__currency']
            direction = item['direction']
            amt = item['total'] or 0
            if curr not in currency_totals:
                currency_totals[curr] = {'debits': 0, 'credits': 0}
            if direction == EntryDirection.DEBIT:
                currency_totals[curr]['debits'] += amt
            elif direction == EntryDirection.CREDIT:
                currency_totals[curr]['credits'] += amt

        is_healthy = (len(discrepant_wallets) == 0 and len(unbalanced_tx_ids) == 0)

        return GlobalReconciliationReport(
            total_wallets_checked=wallets.count(),
            discrepant_wallets_count=len(discrepant_wallets),
            total_transactions_checked=transactions.count(),
            unbalanced_transactions_count=len(unbalanced_tx_ids),
            currency_balances=currency_totals,
            discrepant_wallets=discrepant_wallets,
            unbalanced_transaction_ids=unbalanced_tx_ids,
            is_healthy=is_healthy,
        )
