from django.db import models
from django.conf import settings
from apps.wallets.models import Wallet
from apps.common.money import minor_units_to_decimal


class RiskAction(models.TextChoices):
    ALLOW = 'ALLOW', 'Allow'
    FLAG = 'FLAG', 'Flag (Manual Review)'
    BLOCK = 'BLOCK', 'Block (Rejected)'


class RiskRuleConfig(models.Model):
    """
    Database-backed configuration for pluggable fraud rules.
    Permits dynamic runtime reconfiguration of risk thresholds.
    """
    rule_id = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=128)
    is_enabled = models.BooleanField(default=True)
    parameters = models.JSONField(
        default=dict,
        help_text='JSON dictionary of dynamic rule thresholds (e.g. max_transfers, window_minutes, max_amount)'
    )
    description = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'risk_rule_configs'
        ordering = ['rule_id']

    def __str__(self):
        status = "Active" if self.is_enabled else "Disabled"
        return f"{self.name} [{self.rule_id}] - {status}"


class RiskDecision(models.Model):
    """
    Persistent audit record of an automated fraud risk assessment.
    Stores complete decision trace including evaluated rules and triggering violations.
    """
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='risk_decisions')
    operation_type = models.CharField(max_length=32, db_index=True)
    amount = models.BigIntegerField(help_text='Operation amount in minor units')
    decision = models.CharField(
        max_length=10,
        choices=RiskAction.choices,
        db_index=True
    )
    triggering_rule_ids = models.JSONField(
        default=list,
        help_text='List of rule IDs that triggered FLAG or BLOCK'
    )
    rule_evaluations = models.JSONField(
        default=dict,
        help_text='Detailed breakdown of all evaluated rules and reasons'
    )
    context_data = models.JSONField(default=dict)
    is_reviewed = models.BooleanField(default=False, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_risk_decisions'
    )
    review_notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'risk_decisions'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"RiskDecision #{self.id} [{self.operation_type} - {self.decision} - Wallet {self.wallet_id}]"

    @property
    def amount_decimal(self):
        return minor_units_to_decimal(self.amount, self.wallet.currency)
