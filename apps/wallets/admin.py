from django.contrib import admin
from .models import Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'user',
        'currency',
        'state',
        'display_available_balance',
        'display_reserved_balance',
        'display_total_balance',
        'created_at',
    )
    list_filter = ('currency', 'state', 'created_at')
    search_fields = ('user__username', 'user__email', 'id')
    ordering = ('id',)
    readonly_fields = (
        'available_balance',
        'reserved_balance',
        'display_available_balance',
        'display_reserved_balance',
        'display_total_balance',
        'created_at',
        'updated_at',
    )
    fieldsets = (
        ('Wallet Overview', {
            'fields': ('user', 'currency', 'state', 'daily_limit')
        }),
        ('Balances (Minor Units & Decimal)', {
            'fields': (
                ('available_balance', 'display_available_balance'),
                ('reserved_balance', 'display_reserved_balance'),
                'display_total_balance',
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    @admin.display(description="Available (Decimal)")
    def display_available_balance(self, obj):
        return f"{obj.currency} {obj.available_balance_decimal}"

    @admin.display(description="Reserved (Decimal)")
    def display_reserved_balance(self, obj):
        return f"{obj.currency} {obj.reserved_balance_decimal}"

    @admin.display(description="Total (Decimal)")
    def display_total_balance(self, obj):
        return f"{obj.currency} {obj.total_balance_decimal}"

