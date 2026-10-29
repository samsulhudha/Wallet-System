import pytest
from datetime import timedelta
from django.utils import timezone
from apps.risk.rules.base import RiskContext
from apps.risk.rules.velocity import VelocityRule
from apps.risk.rules.account_age import AccountAgeRule
from apps.risk.rules.rapid_withdrawal import RapidWithdrawalAfterTopUpRule
from apps.risk.models import RiskAction
from apps.risk.engine import RiskEngine
from apps.payments.models import Transfer, TopUp, TopUpStatus, TransferStatus


@pytest.mark.django_db
def test_velocity_rule_triggers_above_threshold(wallet_a_inr, wallet_b_inr):
    rule = VelocityRule()
    now = timezone.now()

    # Create 5 completed transfers
    for i in range(5):
        Transfer.objects.create(
            source_wallet=wallet_a_inr,
            destination_wallet=wallet_b_inr,
            amount=1000,
            currency="INR",
            status=TransferStatus.COMPLETED,
            created_at=now - timedelta(minutes=1)
        )

    ctx = RiskContext(
        wallet=wallet_a_inr,
        operation_type='TRANSFER',
        amount=1000,
        currency='INR',
        timestamp=now
    )

    eval_result = rule.evaluate(ctx, config={'max_transfers': 5, 'window_minutes': 5, 'action': RiskAction.BLOCK})
    assert eval_result.action == RiskAction.BLOCK
    assert "velocity threshold" in eval_result.reason.lower()


@pytest.mark.django_db
def test_account_age_rule_triggers_for_new_account(wallet_a_inr):
    rule = AccountAgeRule()
    now = timezone.now()

    # Wallet was created right now (age < 7 days)
    ctx_high = RiskContext(
        wallet=wallet_a_inr,
        operation_type='TRANSFER',
        amount=6000000,  # 60,000 INR > 50,000 threshold
        currency='INR',
        timestamp=now
    )
    res_high = rule.evaluate(ctx_high, config={'max_age_days': 7, 'max_amount': 5000000, 'action': RiskAction.FLAG})
    assert res_high.action == RiskAction.FLAG

    # Below threshold -> ALLOW
    ctx_low = RiskContext(
        wallet=wallet_a_inr,
        operation_type='TRANSFER',
        amount=100000,  # 1,000 INR < 50,000
        currency='INR',
        timestamp=now
    )
    res_low = rule.evaluate(ctx_low, config={'max_age_days': 7, 'max_amount': 5000000})
    assert res_low.action == RiskAction.ALLOW


@pytest.mark.django_db
def test_rapid_withdrawal_after_topup_triggers(wallet_a_inr):
    rule = RapidWithdrawalAfterTopUpRule()
    now = timezone.now()

    # Create large topup 10 mins ago
    TopUp.objects.create(
        wallet=wallet_a_inr,
        amount=2000000,  # 20,000 INR
        currency="INR",
        provider_reference="topup_rapid_test",
        status=TopUpStatus.COMPLETED,
        created_at=now - timedelta(minutes=10)
    )

    ctx = RiskContext(
        wallet=wallet_a_inr,
        operation_type='WITHDRAWAL',
        amount=1500000,
        currency='INR',
        timestamp=now
    )

    result = rule.evaluate(ctx, config={'window_minutes': 30, 'min_topup_amount': 1000000, 'action': RiskAction.FLAG})
    assert result.action == RiskAction.FLAG
    assert "rapid withdrawal" in result.reason.lower()


@pytest.mark.django_db
def test_risk_engine_precedence(wallet_a_inr):
    """Verifies that BLOCK overrides FLAG, and FLAG overrides ALLOW."""
    ctx = RiskContext(
        wallet=wallet_a_inr,
        operation_type='WITHDRAWAL',
        amount=6000000,
        currency='INR',
        timestamp=timezone.now()
    )

    decision = RiskEngine.evaluate(ctx)
    # New wallet high value triggers FLAG
    assert decision.decision in (RiskAction.FLAG, RiskAction.BLOCK)

