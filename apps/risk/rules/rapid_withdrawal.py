from datetime import timedelta
from django.conf import settings
from .base import BaseRiskRule, RiskContext, RuleEvaluation
from apps.risk.models import RiskAction
from apps.payments.models import TopUp, TopUpStatus


class RapidWithdrawalAfterTopUpRule(BaseRiskRule):
    """
    Detects potential fraud/money mule activity when a withdrawal is initiated
    shortly after a substantial incoming top-up.
    """
    rule_id = "rule_rapid_withdrawal_after_topup"
    name = "Rapid Withdrawal Following Large Top-up Rule"

    def evaluate(self, context: RiskContext, config: dict = None) -> RuleEvaluation:
        if context.operation_type != 'WITHDRAWAL':
            return RuleEvaluation(rule_id=self.rule_id, action=RiskAction.ALLOW, reason="Not a withdrawal operation.")

        cfg = config or {}
        window_minutes = cfg.get('window_minutes', getattr(settings, 'RAPID_WITHDRAWAL_WINDOW_MINUTES', 30))
        min_topup_amount = cfg.get('min_topup_amount', getattr(settings, 'RAPID_WITHDRAWAL_MIN_TOPUP_PAISE', 1000000))
        action_on_trigger = cfg.get('action', RiskAction.FLAG)

        cutoff = context.timestamp - timedelta(minutes=window_minutes)
        recent_large_topup = TopUp.objects.filter(
            wallet=context.wallet,
            status=TopUpStatus.COMPLETED,
            amount__gte=min_topup_amount,
            created_at__gte=cutoff
        ).first()

        if recent_large_topup:
            return RuleEvaluation(
                rule_id=self.rule_id,
                action=action_on_trigger,
                reason=(
                    f"Rapid withdrawal of {context.amount} requested within {window_minutes} minutes "
                    f"of large top-up #{recent_large_topup.id} ({recent_large_topup.amount})."
                ),
                metadata={
                    'topup_id': recent_large_topup.id,
                    'topup_amount': recent_large_topup.amount,
                    'window_minutes': window_minutes,
                }
            )

        return RuleEvaluation(
            rule_id=self.rule_id,
            action=RiskAction.ALLOW,
            reason="No rapid withdrawal pattern detected."
        )

