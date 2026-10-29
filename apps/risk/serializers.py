from rest_framework import serializers
from .models import RiskDecision, RiskRuleConfig


class RiskDecisionSerializer(serializers.ModelSerializer):
    amount_decimal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    username = serializers.CharField(source='wallet.user.username', read_only=True)
    currency = serializers.CharField(source='wallet.currency', read_only=True)

    class Meta:
        model = RiskDecision
        fields = (
            'id', 'wallet', 'username', 'currency', 'operation_type', 'amount',
            'amount_decimal', 'decision', 'triggering_rule_ids', 'rule_evaluations',
            'context_data', 'is_reviewed', 'reviewed_by', 'review_notes', 'created_at'
        )
        read_only_fields = fields


class RiskRuleConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskRuleConfig
        fields = ('id', 'rule_id', 'name', 'is_enabled', 'parameters', 'description', 'updated_at')
