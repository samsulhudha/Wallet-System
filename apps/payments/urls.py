from django.urls import path
from .views import (
    TopUpView,
    TransferView,
    WithdrawalRequestView,
    WithdrawalDetailView,
    BankCallbackWebhookView,
    AdminTransferReversalView,
)

urlpatterns = [
    # Topups
    path('wallets/<int:pk>/topups/', TopUpView.as_view(), name='wallet_topup'),

    # Transfers
    path('transfers/', TransferView.as_view(), name='transfer_funds'),

    # Withdrawals
    path('wallets/<int:pk>/withdrawals/', WithdrawalRequestView.as_view(), name='wallet_withdrawal'),
    path('withdrawals/<int:pk>/', WithdrawalDetailView.as_view(), name='withdrawal_detail'),

    # Bank Callbacks
    path('payments/callbacks/bank/', BankCallbackWebhookView.as_view(), name='bank_callback'),

    # Admin Reversals
    path('admin/transfers/<int:pk>/reverse/', AdminTransferReversalView.as_view(), name='admin_transfer_reverse'),
]
