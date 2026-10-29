from django.contrib import admin
from apps.common.money import minor_units_to_decimal
from .models import LedgerAccount, LedgerTransaction, LedgerEntry


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    can_delete = False
    readonly_fields = ('id', 'account', 'direction', 'amount', 'display_amount', 'created_at')

    @admin.display(description="Amount (Decimal)")
    def display_amount(self, obj):
        return f"{obj.account.currency} {minor_units_to_decimal(obj.amount, obj.account.currency)}"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'account_type', 'currency', 'wallet', 'created_at')
    list_filter = ('account_type', 'currency', 'created_at')
    search_fields = ('name', 'wallet__id', 'wallet__user__username')
    ordering = ('id',)
    readonly_fields = ('created_at',)


@admin.register(LedgerTransaction)
class LedgerTransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'transaction_type', 'reference_id', 'currency', 'description', 'posted_at')
    list_filter = ('transaction_type', 'currency', 'posted_at')
    search_fields = ('reference_id', 'description', 'id')
    ordering = ('-posted_at', '-id')
    readonly_fields = ('reference_id', 'transaction_type', 'currency', 'description', 'posted_at')
    inlines = [LedgerEntryInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'transaction',
        'account',
        'direction',
        'amount',
        'display_amount',
        'created_at',
    )
    list_filter = ('direction', 'account__account_type', 'account__currency', 'created_at')
    search_fields = ('transaction__reference_id', 'account__name', 'id')
    ordering = ('-id',)
    readonly_fields = ('transaction', 'account', 'direction', 'amount', 'display_amount', 'created_at')

    @admin.display(description="Amount (Decimal)")
    def display_amount(self, obj):
        return f"{obj.account.currency} {minor_units_to_decimal(obj.amount, obj.account.currency)}"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

