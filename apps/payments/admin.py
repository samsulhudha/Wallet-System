from django.contrib import admin
from .models import (
    IdempotencyRecord,
    TopUp,
    Transfer,
    Withdrawal,
    WithdrawalJob,
    BankCallbackLog,
)


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'idempotency_key', 'endpoint', 'status', 'response_code', 'created_at')
    list_filter = ('status', 'endpoint', 'created_at')
    search_fields = ('idempotency_key', 'user__username', 'endpoint')
    readonly_fields = (
        'user',
        'idempotency_key',
        'endpoint',
        'request_hash',
        'status',
        'response_code',
        'response_data',
        'resource_id',
        'created_at',
        'updated_at',
    )
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False


@admin.register(TopUp)
class TopUpAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'display_amount', 'status', 'provider_reference', 'created_at', 'completed_at')
    list_filter = ('status', 'currency', 'created_at')
    search_fields = ('provider_reference', 'wallet__id', 'wallet__user__username')
    ordering = ('-created_at', '-id')
    readonly_fields = ('wallet', 'amount', 'display_amount', 'currency', 'provider_reference', 'created_at', 'completed_at')

    @admin.display(description="Amount (Decimal)")
    def display_amount(self, obj):
        return f"{obj.currency} {obj.amount_decimal}"

    def has_add_permission(self, request):
        return False


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'source_wallet',
        'destination_wallet',
        'display_amount',
        'status',
        'is_reversed',
        'created_at',
    )
    list_filter = ('status', 'is_reversed', 'currency', 'created_at')
    search_fields = ('id', 'source_wallet__id', 'destination_wallet__id', 'description')
    ordering = ('-created_at', '-id')
    readonly_fields = (
        'source_wallet',
        'destination_wallet',
        'amount',
        'display_amount',
        'currency',
        'status',
        'is_reversed',
        'created_at',
    )

    @admin.display(description="Amount (Decimal)")
    def display_amount(self, obj):
        return f"{obj.currency} {obj.amount_decimal}"

    def has_add_permission(self, request):
        return False


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'display_amount', 'status', 'provider_reference', 'created_at')
    list_filter = ('status', 'currency', 'created_at')
    search_fields = ('provider_reference', 'wallet__id', 'wallet__user__username', 'bank_account_info')
    ordering = ('-created_at', '-id')
    readonly_fields = (
        'wallet',
        'amount',
        'display_amount',
        'currency',
        'provider_reference',
        'bank_account_info',
        'created_at',
        'updated_at',
    )

    @admin.display(description="Amount (Decimal)")
    def display_amount(self, obj):
        return f"{obj.currency} {obj.amount_decimal}"

    def has_add_permission(self, request):
        return False


@admin.register(WithdrawalJob)
class WithdrawalJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'withdrawal', 'status', 'attempts', 'max_attempts', 'next_attempt_at', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('withdrawal__id', 'withdrawal__provider_reference', 'last_error')
    ordering = ['next_attempt_at', 'id']
    readonly_fields = ('withdrawal', 'created_at', 'updated_at')


@admin.register(BankCallbackLog)
class BankCallbackLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'provider_event_id', 'provider_reference', 'event_type', 'processed_at')
    list_filter = ('event_type', 'processed_at')
    search_fields = ('provider_event_id', 'provider_reference')
    ordering = ('-processed_at',)
    readonly_fields = ('provider_event_id', 'provider_reference', 'event_type', 'payload', 'processed_at')

    def has_add_permission(self, request):
        return False

