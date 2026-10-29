from datetime import timedelta
from django.conf import settings
from .base import BaseRiskRule, RiskContext, RuleEvaluation
from apps.risk.models import RiskAction


class AccountAgeRule(BaseRiskRule):
    """
    Evaluates whether a high-value transfer or withdrawal is attempted on an account
    younger than Y days.
    """
    rule_id = "rule_account_age_high_value"
    name = "New Account High Value Transaction Rule"

    def evaluate(self, context: RiskContext, config: dict = None) -> RuleEvaluation:
        cfg = config or {}
        max_age_days = cfg.get('max_age_days', getattr(settings, 'NEW_ACCOUNT_AGE_DAYS', 7))
        max_amount = cfg.get('max_amount', getattr(settings, 'NEW_ACCOUNT_MAX_AMOUNT_PAISE', 5000000))
        action_on_trigger = cfg.get('action', RiskAction.FLAG)

        wallet_age = context.timestamp - context.wallet.created_at
        is_young = wallet_age < timedelta(days=max_age_days)

        if is_young and context.amount > max_amount:
            return RuleEvaluation(
                rule_id=self.rule_id,
                action=action_on_trigger,
                reason=(
                    f"High-value {context.operation_type} of {context.amount} on new wallet "
                    f"created {wallet_age.days} days ago (Threshold: {max_age_days} days, Max: {max_amount})."
                ),
                metadata={
                    'wallet_age_days': wallet_age.days,
                    'max_age_days': max_age_days,
                    'amount': context.amount,
                    'max_amount': max_amount,
                }
            )

        return RuleEvaluation(
            rule_id=self.rule_id,
            action=RiskAction.ALLOW,
            reason="Account age and transaction value within normal limits."
        )

