from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional
from apps.wallets.models import Wallet
from apps.risk.models import RiskAction


@dataclass
class RiskContext:
    wallet: Wallet
    operation_type: str  # 'TRANSFER' or 'WITHDRAWAL'
    amount: int          # minor units
    currency: str
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleEvaluation:
    rule_id: str
    action: str  # RiskAction.ALLOW, FLAG, or BLOCK
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseRiskRule(ABC):
    rule_id: str = "base_rule"
    name: str = "Base Risk Rule"

    @abstractmethod
    def evaluate(self, context: RiskContext, config: Optional[Dict[str, Any]] = None) -> RuleEvaluation:
        """
        Evaluates risk for given transaction context.
        Must return RuleEvaluation with action ALLOW, FLAG, or BLOCK.
        """
        pass

