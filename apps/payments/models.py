import hashlib
import json
from django.db import models
from django.conf import settings
from django.db.models import UniqueConstraint, CheckConstraint, Q
from apps.wallets.models import Wallet
from apps.common.money import minor_units_to_decimal


class IdempotencyStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    RESOLVED = 'RESOLVED', 'Resolved'
    FAILED = 'FAILED', 'Failed'


class IdempotencyRecord(models.Model):
    """
    Persistent idempotency tracker scoped by authenticated principal and endpoint.
    Prevents double execution of money-moving operations.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='idempotency_records'
    )
    idempotency_key = models.CharField(max_length=255, db_index=True)
    endpoint = models.CharField(max_length=128, db_index=True)
    request_hash = models.CharField(
        max_length=64,
        help_text='SHA-256 hash of canonicalized JSON request payload'
    )
    status = models.CharField(
        max_length=16,
        choices=IdempotencyStatus.choices,
        default=IdempotencyStatus.PENDING,
        db_index=True
    )
    response_code = models.PositiveIntegerField(null=True, blank=True)
    response_data = models.JSONField(null=True, blank=True)
    resource_id = models.CharField(max_length=128, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'idempotency_records'
        constraints = [
            UniqueConstraint(
                fields=['user', 'idempotency_key', 'endpoint'],
                name='unique_user_key_endpoint'
            )
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"IdempotencyRecord ({self.user.username} - {self.idempotency_key} - {self.status})"

    @staticmethod
    def compute_request_hash(data: dict) -> str:
        """Computes deterministic SHA-256 hash of normalized dictionary."""
        if not data:
            serialized = "{}"
        else:
            serialized = json.dumps(data, sort_keys=True, separators=(',', ':'), default=str)
        return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


class TopUpStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'


class TopUp(models.Model):
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='topups')
    amount = models.BigIntegerField(help_text='Top-up amount in minor units')
    currency = models.CharField(max_length=3)
    provider_reference = models.CharField(max_length=128, unique=True, db_index=True)
    status = models.CharField(max_length=16, choices=TopUpStatus.choices, default=TopUpStatus.PENDING)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'topups'
        ordering = ['-created_at', '-id']

    @property
    def amount_decimal(self):
        return minor_units_to_decimal(self.amount, self.currency)


class TransferStatus(models.TextChoices):
    COMPLETED = 'COMPLETED', 'Completed'
    REVERSED = 'REVERSED', 'Reversed'


class Transfer(models.Model):
    source_wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='outgoing_transfers')
    destination_wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='incoming_transfers')
    amount = models.BigIntegerField(help_text='Transfer amount in minor units')
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=16, choices=TransferStatus.choices, default=TransferStatus.COMPLETED)
    is_reversed = models.BooleanField(default=False, db_index=True)
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'transfers'
        ordering = ['-created_at', '-id']

    @property
    def amount_decimal(self):
        return minor_units_to_decimal(self.amount, self.currency)


class WithdrawalStatus(models.TextChoices):
    REQUESTED = 'REQUESTED', 'Requested'
    PROCESSING = 'PROCESSING', 'Processing'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'


class Withdrawal(models.Model):
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='withdrawals')
    amount = models.BigIntegerField(help_text='Withdrawal amount in minor units')
    currency = models.CharField(max_length=3)
    provider_reference = models.CharField(max_length=128, unique=True, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=WithdrawalStatus.choices,
        default=WithdrawalStatus.REQUESTED,
        db_index=True
    )
    bank_account_info = models.CharField(max_length=255, blank=True, default='')
    failure_reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'withdrawals'
        ordering = ['-created_at', '-id']

    @property
    def amount_decimal(self):
        return minor_units_to_decimal(self.amount, self.currency)


class WithdrawalJobStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    PROCESSING = 'PROCESSING', 'Processing'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'
    NEEDS_RECONCILIATION = 'NEEDS_RECONCILIATION', 'Needs Reconciliation'


class WithdrawalJob(models.Model):
    """
    Durable PostgreSQL-backed queue table for outbox processing of withdrawals.
    Worker processes pick jobs using SELECT FOR UPDATE SKIP LOCKED.
    """
    withdrawal = models.OneToOneField(Withdrawal, on_delete=models.CASCADE, related_name='job')
    status = models.CharField(
        max_length=24,
        choices=WithdrawalJobStatus.choices,
        default=WithdrawalJobStatus.PENDING,
        db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    next_attempt_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'withdrawal_jobs'
        ordering = ['next_attempt_at', 'id']


class BankCallbackLog(models.Model):
    """
    Deduplication log for bank webhooks/callbacks.
    Unique constraint on provider_event_id ensures duplicate events cannot be processed twice.
    """
    provider_event_id = models.CharField(max_length=128, unique=True, db_index=True)
    provider_reference = models.CharField(max_length=128, db_index=True)
    event_type = models.CharField(max_length=32)
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bank_callback_logs'
        ordering = ['-processed_at']

