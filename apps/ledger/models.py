from django.db import models
from django.db.models import Q, CheckConstraint, UniqueConstraint
from django.core.exceptions import PermissionDenied


class LedgerAccountType(models.TextChoices):
    CUSTOMER_WALLET_AVAILABLE = 'CUSTOMER_WALLET_AVAILABLE', 'Customer Wallet Available'
    CUSTOMER_WALLET_RESERVED = 'CUSTOMER_WALLET_RESERVED', 'Customer Wallet Reserved'
    PAYMENT_GATEWAY_CLEARING = 'PAYMENT_GATEWAY_CLEARING', 'Payment Gateway Clearing (Asset)'
    BANK_SETTLEMENT_CLEARING = 'BANK_SETTLEMENT_CLEARING', 'Bank Settlement Clearing (Asset)'
    ADJUSTMENT_CLEARING = 'ADJUSTMENT_CLEARING', 'Adjustment Clearing'


class TransactionType(models.TextChoices):
    TOPUP = 'TOPUP', 'Wallet Top-up'
    TRANSFER = 'TRANSFER', 'P2P Transfer'
    WITHDRAWAL_RESERVE = 'WITHDRAWAL_RESERVE', 'Withdrawal Fund Reservation'
    WITHDRAWAL_SETTLE = 'WITHDRAWAL_SETTLE', 'Withdrawal Bank Settlement'
    WITHDRAWAL_REFUND = 'WITHDRAWAL_REFUND', 'Withdrawal Failure Refund'
    TRANSFER_REVERSAL = 'TRANSFER_REVERSAL', 'Transfer Reversal'
    ADJUSTMENT = 'ADJUSTMENT', 'Audit Adjustment'


class EntryDirection(models.TextChoices):
    DEBIT = 'DEBIT', 'Debit'
    CREDIT = 'CREDIT', 'Credit'


class LedgerAccount(models.Model):
    """
    Individual double-entry ledger account.
    Can be tied directly to a specific customer Wallet (for available or reserved balances)
    or represent external clearing accounts (Payment Gateway or Bank).
    """
    wallet = models.ForeignKey(
        'wallets.Wallet',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ledger_accounts',
        help_text='Associated customer wallet if applicable'
    )
    account_type = models.CharField(
        max_length=32,
        choices=LedgerAccountType.choices,
        db_index=True
    )
    currency = models.CharField(max_length=3, db_index=True)
    name = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ledger_accounts'
        constraints = [
            UniqueConstraint(
                fields=['wallet', 'account_type', 'currency'],
                name='unique_wallet_account_type_currency',
                condition=Q(wallet__isnull=False)
            ),
            UniqueConstraint(
                fields=['account_type', 'currency'],
                name='unique_system_clearing_account_currency',
                condition=Q(wallet__isnull=True)
            ),
        ]
        ordering = ['id']

    def __str__(self):
        if self.wallet_id:
            return f"LedgerAccount #{self.id} [Wallet {self.wallet_id} - {self.account_type} - {self.currency}]"
        return f"LedgerAccount #{self.id} [System - {self.account_type} - {self.currency}]"


class LedgerTransaction(models.Model):
    """
    Header record representing an atomic financial event comprising multiple balancing entries.
    Every posted transaction MUST balance to zero: Sum(Debits) == Sum(Credits).
    """
    reference_id = models.CharField(
        max_length=128,
        db_index=True,
        help_text='External or domain reference (e.g. transfer ID, topup ID, withdrawal ID)'
    )
    transaction_type = models.CharField(
        max_length=32,
        choices=TransactionType.choices,
        db_index=True
    )
    currency = models.CharField(max_length=3, db_index=True)
    description = models.TextField(blank=True, default='')
    posted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'ledger_transactions'
        ordering = ['-posted_at', '-id']

    def __str__(self):
        return f"LedgerTx #{self.id} ({self.transaction_type} - {self.reference_id} - {self.currency})"


class LedgerEntry(models.Model):
    """
    Immutable individual credit or debit entry belonging to a LedgerTransaction.
    Cannot be modified or deleted once created.
    """
    transaction = models.ForeignKey(
        LedgerTransaction,
        on_delete=models.PROTECT,
        related_name='entries'
    )
    account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name='entries'
    )
    direction = models.CharField(
        max_length=6,
        choices=EntryDirection.choices
    )
    amount = models.BigIntegerField(
        help_text='Positive amount in minor units (e.g. paise)'
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'ledger_entries'
        constraints = [
            CheckConstraint(
                check=Q(amount__gt=0),
                name='check_ledger_entry_amount_positive'
            ),
        ]
        ordering = ['id']

    def __str__(self):
        return f"LedgerEntry #{self.id} ({self.direction} {self.amount} on Account {self.account_id})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionDenied("Ledger entries are strictly immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Ledger entries are strictly immutable and cannot be deleted.")
