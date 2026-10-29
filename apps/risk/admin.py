from django.contrib import admin
from .models import RiskRuleConfig, RiskDecision


@admin.register(RiskRuleConfig)
class RiskRuleConfigAdmin(admin.ModelAdmin):
    list_display = ('rule_id', 'name', 'is_enabled', 'parameters', 'updated_at')
    list_filter = ('is_enabled', 'updated_at')
    search_fields = ('rule_id', 'name', 'description')
    ordering = ('rule_id',)
    readonly_fields = ('updated_at',)


@admin.register(RiskDecision)
class RiskDecisionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'wallet',
        'operation_type',
        'display_amount',
        'decision',
        'triggering_rule_ids',
        'is_reviewed',
        'reviewed_by',
        'created_at',
    )
    list_filter = ('decision', 'is_reviewed', 'operation_type', 'created_at')
    search_fields = ('wallet__id', 'operation_type', 'triggering_rule_ids')
    ordering = ('-created_at', '-id')
    readonly_fields = (
        'wallet',
        'operation_type',
        'amount',
        'display_amount',
        'decision',
        'triggering_rule_ids',
        'rule_evaluations',
        'context_data',
        'created_at',
    )

    @admin.display(description="Amount (Decimal)")
    def display_amount(self, obj):
        return f"{obj.wallet.currency} {obj.amount_decimal}"

    def has_add_permission(self, request):
        return False

