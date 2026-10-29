from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from .base import BaseRiskRule, RiskContext, RuleEvaluation
from apps.risk.models import RiskAction
from apps.payments.models import Transfer


class VelocityRule(BaseRiskRule):
    """
    Flags or blocks if more than N outgoing transfers occur within M minutes.
    Concurrency safety is guaranteed because the caller holds the wallet row lock.
    """
    rule_id = "rule_velocity_transfers"
    name = "Outgoing Transfer Velocity Rule"

    def evaluate(self, context: RiskContext, config: dict = None) -> RuleEvaluation:
        if context.operation_type != 'TRANSFER':
            return RuleEvaluation(rule_id=self.rule_id, action=RiskAction.ALLOW, reason="Not a transfer operation.")

        cfg = config or {}
        max_transfers = cfg.get('max_transfers', getattr(settings, 'VELOCITY_MAX_TRANSFERS', 5))
        window_minutes = cfg.get('window_minutes', getattr(settings, 'VELOCITY_WINDOW_MINUTES', 5))
        action_on_trigger = cfg.get('action', RiskAction.BLOCK)

        cutoff = context.timestamp - timedelta(minutes=window_minutes)
        recent_transfers_count = Transfer.objects.filter(
            source_wallet=context.wallet,
            created_at__gte=cutoff
        ).count()

        if recent_transfers_count >= max_transfers:
            return RuleEvaluation(
                rule_id=self.rule_id,
                action=action_on_trigger,
                reason=f"Exceeded velocity threshold: {recent_transfers_count} transfers in last {window_minutes} minutes (Limit: {max_transfers}).",
                metadata={
                    'recent_transfers_count': recent_transfers_count,
                    'max_transfers': max_transfers,
                    'window_minutes': window_minutes,
                }
            )

        return RuleEvaluation(
            rule_id=self.rule_id,
            action=RiskAction.ALLOW,
            reason="Velocity is within normal limits.",
            metadata={'recent_transfers_count': recent_transfers_count}
        )

