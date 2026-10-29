from rest_framework import permissions


class IsAdminUserOrReadOnly(permissions.BasePermission):
    """Allows full access to staff/admin users, read-only to others."""
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return bool(request.user and request.user.is_staff)


class IsAdminUserOnly(permissions.BasePermission):
    """Allows access exclusively to staff/admin users."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsWalletOwnerOrAdmin(permissions.BasePermission):
    """
    Object-level permission allowing wallet owners to access their wallet,
    or administrators to access any wallet.
    """
    def has_object_permission(self, request, view, obj):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.is_staff:
            return True
        owner = getattr(obj, 'user', None) or getattr(obj, 'owner', None)
        return owner == request.user

