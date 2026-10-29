from django.urls import path
from .views import (
    WalletListCreateView,
    WalletDetailView,
    WalletTransactionsStatementView,
    AdminFreezeWalletView,
    AdminUnfreezeWalletView,
    AdminCloseWalletView,
)

urlpatterns = [
    # Wallet core
    path('wallets/', WalletListCreateView.as_view(), name='wallet_list_create'),
    path('wallets/<int:pk>/', WalletDetailView.as_view(), name='wallet_detail'),
    path('wallets/<int:pk>/transactions/', WalletTransactionsStatementView.as_view(), name='wallet_statements'),

    # Admin wallet state management
    path('admin/wallets/<int:pk>/freeze/', AdminFreezeWalletView.as_view(), name='admin_wallet_freeze'),
    path('admin/wallets/<int:pk>/unfreeze/', AdminUnfreezeWalletView.as_view(), name='admin_wallet_unfreeze'),
    path('admin/wallets/<int:pk>/close/', AdminCloseWalletView.as_view(), name='admin_wallet_close'),
]
