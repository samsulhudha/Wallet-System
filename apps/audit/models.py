from django.db import models
from django.conf import settings
from django.core.exceptions import PermissionDenied
from apps.wallets.models import Wallet


class WalletAuditLog(models.Model):
    """
    Append-only immutable audit trail for wallet lifecycle state transitions.
    """
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='state_history')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='wallet_state_changes'
    )
    previous_state = models.CharField(max_length=16)
    new_state = models.CharField(max_length=16)
    reason = models.TextField(help_text='Mandatory operational justification')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'wallet_audit_logs'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"WalletAudit #{self.id} [Wallet {self.wallet_id}: {self.previous_state} -> {self.new_state}]"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionDenied("Wallet audit logs are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Wallet audit logs are immutable and cannot be deleted.")


class AdminAuditLog(models.Model):
    """
    Append-only audit record for privileged administrative financial actions.
    """
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='admin_audit_logs'
    )
    action = models.CharField(max_length=64, db_index=True)
    target_model = models.CharField(max_length=64)
    target_id = models.CharField(max_length=64)
    reason = models.TextField(help_text='Mandatory administrative reason')
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'admin_audit_logs'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"AdminAudit #{self.id} [{self.actor.username} - {self.action} on {self.target_model} #{self.target_id}]"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionDenied("Admin audit logs are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Admin audit logs are immutable and cannot be deleted.")

