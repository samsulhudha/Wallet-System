import logging
from typing import List, Dict, Type
from django.utils import timezone
from .rules.base import BaseRiskRule, RiskContext, RuleEvaluation
from .rules.velocity import VelocityRule
from .rules.account_age import AccountAgeRule
from .rules.rapid_withdrawal import RapidWithdrawalAfterTopUpRule
from .models import RiskDecision, RiskRuleConfig, RiskAction
from apps.wallets.models import Wallet
from apps.common.exceptions import FraudDetectedError

logger = logging.getLogger(__name__)


class RiskEngine:
    """
    Central pluggable fraud and risk evaluation engine.
    Enforces decision precedence: BLOCK > FLAG > ALLOW.
    Persists decision history transactionally.
    """

    _registry: Dict[str, BaseRiskRule] = {}

    @classmethod
    def register_rule(cls, rule: BaseRiskRule):
        cls._registry[rule.rule_id] = rule
        logger.info("Registered risk rule: %s (%s)", rule.name, rule.rule_id)

    @classmethod
    def get_registered_rules(cls) -> List[BaseRiskRule]:
        if not cls._registry:
            cls.register_default_rules()
        return list(cls._registry.values())

    @classmethod
    def register_default_rules(cls):
        cls.register_rule(VelocityRule())
        cls.register_rule(AccountAgeRule())
        cls.register_rule(RapidWithdrawalAfterTopUpRule())

    @classmethod
    def evaluate(cls, context: RiskContext) -> RiskDecision:
        """
        Executes all active rules against the transaction context.
        Applies precedence: BLOCK > FLAG > ALLOW.
        Creates and returns a persisted RiskDecision record.
        """
        rules = cls.get_registered_rules()

        # Fetch DB rule configurations
        rule_configs = {
            cfg.rule_id: cfg
            for cfg in RiskRuleConfig.objects.filter(rule_id__in=[r.rule_id for r in rules])
        }

        final_action = RiskAction.ALLOW
        triggering_rule_ids = []
        rule_evaluations_dict = {}

        for rule in rules:
            cfg = rule_configs.get(rule.rule_id)
            if cfg and not cfg.is_enabled:
                logger.debug("Skipping disabled rule %s", rule.rule_id)
                continue

            config_params = cfg.parameters if cfg else {}
            evaluation: RuleEvaluation = rule.evaluate(context, config=config_params)

            rule_evaluations_dict[rule.rule_id] = {
                'action': evaluation.action,
                'reason': evaluation.reason,
                'metadata': evaluation.metadata,
            }

            if evaluation.action == RiskAction.BLOCK:
                final_action = RiskAction.BLOCK
                triggering_rule_ids.append(rule.rule_id)
            elif evaluation.action == RiskAction.FLAG and final_action != RiskAction.BLOCK:
                final_action = RiskAction.FLAG
                triggering_rule_ids.append(rule.rule_id)

        decision_record = RiskDecision.objects.create(
            wallet=context.wallet,
            operation_type=context.operation_type,
            amount=context.amount,
            decision=final_action,
            triggering_rule_ids=triggering_rule_ids,
            rule_evaluations=rule_evaluations_dict,
            context_data=context.metadata,
        )

        logger.info(
            "Risk assessment for Wallet #%s [%s %s]: %s (triggers: %s)",
            context.wallet.id, context.operation_type, context.amount, final_action, triggering_rule_ids
        )

        return decision_record

    @classmethod
    def evaluate_transfer(
        cls,
        wallet: Wallet,
        amount: int,
        currency: str,
        timestamp=None,
        metadata: dict = None
    ) -> RiskDecision:
        ctx = RiskContext(
            wallet=wallet,
            operation_type='TRANSFER',
            amount=amount,
            currency=currency,
            timestamp=timestamp or timezone.now(),
            metadata=metadata or {},
        )
        decision = cls.evaluate(ctx)
        if decision.decision == RiskAction.BLOCK:
            reasons = "; ".join(
                decision.rule_evaluations[r_id]['reason']
                for r_id in decision.triggering_rule_ids
                if r_id in decision.rule_evaluations
            )
            raise FraudDetectedError(
                f"Transfer blocked by fraud security engine: {reasons}",
                details={'decision_id': decision.id, 'triggering_rules': decision.triggering_rule_ids}
            )
        return decision

    @classmethod
    def evaluate_withdrawal(
        cls,
        wallet: Wallet,
        amount: int,
        currency: str,
        timestamp=None,
        metadata: dict = None
    ) -> RiskDecision:
        ctx = RiskContext(
            wallet=wallet,
            operation_type='WITHDRAWAL',
            amount=amount,
            currency=currency,
            timestamp=timestamp or timezone.now(),
            metadata=metadata or {},
        )
        decision = cls.evaluate(ctx)
        if decision.decision == RiskAction.BLOCK:
            reasons = "; ".join(
                decision.rule_evaluations[r_id]['reason']
                for r_id in decision.triggering_rule_ids
                if r_id in decision.rule_evaluations
            )
            raise FraudDetectedError(
                f"Withdrawal blocked by fraud security engine: {reasons}",
                details={'decision_id': decision.id, 'triggering_rules': decision.triggering_rule_ids}
            )
        return decision

