import sys
from django.core.management.base import BaseCommand
from apps.ledger.reconciliation import ReconciliationService


class Command(BaseCommand):
    help = 'Executes end-to-end financial reconciliation verifying ledger balance invariants.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting financial ledger reconciliation..."))
        report = ReconciliationService.run_full_reconciliation()

        self.stdout.write(f"Checked {report.total_wallets_checked} wallets.")
        self.stdout.write(f"Checked {report.total_transactions_checked} ledger transactions.")

        for curr, totals in report.currency_balances.items():
            diff = totals['debits'] - totals['credits']
            status_text = "OK" if diff == 0 else "MISMATCH"
            self.stdout.write(
                f"  [{curr}] Debits: {totals['debits']}, Credits: {totals['credits']} (Diff: {diff}) -> {status_text}"
            )

        if not report.is_healthy:
            self.stdout.write(self.style.ERROR("\nRECONCILIATION FAILURE DETECTED!"))
            if report.discrepant_wallets_count > 0:
                self.stdout.write(self.style.ERROR(f"Discrepant wallets ({report.discrepant_wallets_count}):"))
                for w in report.discrepant_wallets:
                    self.stdout.write(
                        self.style.ERROR(
                            f"  Wallet #{w.wallet_id} ({w.currency}): Cached Avail={w.cached_available}, "
                            f"Expected Avail={w.expected_available} (Diff={w.available_discrepancy}); "
                            f"Cached Res={w.cached_reserved}, Expected Res={w.expected_reserved} (Diff={w.reserved_discrepancy})"
                        )
                    )
            if report.unbalanced_transactions_count > 0:
                self.stdout.write(
                    self.style.ERROR(f"Unbalanced transactions: {report.unbalanced_transaction_ids}")
                )
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("\nRECONCILIATION SUCCESS: All balances match immutable ledger!"))

