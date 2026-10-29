from django.contrib import admin
from .models import WalletAuditLog, AdminAuditLog


@admin.register(WalletAuditLog)
class WalletAuditLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'actor', 'previous_state', 'new_state', 'reason', 'created_at')
    list_filter = ('previous_state', 'new_state', 'created_at')
    search_fields = ('wallet__id', 'actor__username', 'reason')
    ordering = ('-created_at', '-id')
    readonly_fields = ('wallet', 'actor', 'previous_state', 'new_state', 'reason', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'actor', 'action', 'target_model', 'target_id', 'reason', 'created_at')
    list_filter = ('action', 'target_model', 'created_at')
    search_fields = ('actor__username', 'target_id', 'reason', 'action')
    ordering = ('-created_at', '-id')
    readonly_fields = ('actor', 'action', 'target_model', 'target_id', 'reason', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

