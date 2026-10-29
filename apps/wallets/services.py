import logging
from django.db import transaction
from apps.accounts.models import User
from apps.wallets.models import Wallet, WalletState
from apps.audit.models import WalletAuditLog, AdminAuditLog
from apps.payments.models import Withdrawal, WithdrawalStatus
from apps.common.exceptions import DomainError, WalletStateError

logger = logging.getLogger(__name__)


class WalletStateService:
    """
    Administrative domain service managing wallet lifecycle states (ACTIVE, FROZEN, CLOSED).
    Enforces row-level locking to guarantee race-free coordination with concurrent transfers.
    """

    @classmethod
    @transaction.atomic
    def freeze_wallet(cls, wallet_id: int, admin_user: User, reason: str) -> Wallet:
        if not admin_user.is_staff:
            raise DomainError("Only administrators can freeze wallets.", status_code=403)

        if not reason or not reason.strip():
            raise DomainError("A mandatory administrative reason is required to freeze a wallet.")

        wallet = Wallet.objects.select_for_update().get(id=wallet_id)

        if wallet.is_closed:
            raise WalletStateError(f"Cannot freeze closed wallet #{wallet.id}.")

        if wallet.is_frozen:
            return wallet  # Idempotent state

        prev_state = wallet.state
        wallet.state = WalletState.FROZEN
        wallet.save(update_fields=['state', 'updated_at'])

        WalletAuditLog.objects.create(
            wallet=wallet,
            actor=admin_user,
            previous_state=prev_state,
            new_state=WalletState.FROZEN,
            reason=reason.strip()
        )

        AdminAuditLog.objects.create(
            actor=admin_user,
            action='WALLET_FREEZE',
            target_model='Wallet',
            target_id=str(wallet.id),
            reason=reason.strip()
        )

        logger.info("Wallet #%s frozen by admin %s. Reason: %s", wallet.id, admin_user.username, reason)
        return wallet

    @classmethod
    @transaction.atomic
    def unfreeze_wallet(cls, wallet_id: int, admin_user: User, reason: str) -> Wallet:
        if not admin_user.is_staff:
            raise DomainError("Only administrators can unfreeze wallets.", status_code=403)

        if not reason or not reason.strip():
            raise DomainError("A mandatory administrative reason is required to unfreeze a wallet.")

        wallet = Wallet.objects.select_for_update().get(id=wallet_id)

        if wallet.is_closed:
            raise WalletStateError(f"Cannot unfreeze closed wallet #{wallet.id}.")

        if wallet.is_active:
            return wallet  # Idempotent state

        prev_state = wallet.state
        wallet.state = WalletState.ACTIVE
        wallet.save(update_fields=['state', 'updated_at'])

        WalletAuditLog.objects.create(
            wallet=wallet,
            actor=admin_user,
            previous_state=prev_state,
            new_state=WalletState.ACTIVE,
            reason=reason.strip()
        )

        AdminAuditLog.objects.create(
            actor=admin_user,
            action='WALLET_UNFREEZE',
            target_model='Wallet',
            target_id=str(wallet.id),
            reason=reason.strip()
        )

        logger.info("Wallet #%s unfrozen by admin %s. Reason: %s", wallet.id, admin_user.username, reason)
        return wallet

    @classmethod
    @transaction.atomic
    def close_wallet(cls, wallet_id: int, admin_user: User, reason: str) -> Wallet:
        if not admin_user.is_staff:
            raise DomainError("Only administrators can close wallets.", status_code=403)

        if not reason or not reason.strip():
            raise DomainError("A mandatory administrative reason is required to close a wallet.")

        wallet = Wallet.objects.select_for_update().get(id=wallet_id)

        if wallet.is_closed:
            return wallet  # Idempotent

        # Closure prerequisites: zero balances and no pending operations
        if wallet.available_balance != 0 or wallet.reserved_balance != 0:
            raise WalletStateError(
                f"Cannot close Wallet #{wallet.id}: available balance ({wallet.available_balance}) "
                f"and reserved balance ({wallet.reserved_balance}) must be exactly zero."
            )

        has_pending = Withdrawal.objects.filter(
            wallet=wallet,
            status__in=[WithdrawalStatus.REQUESTED, WithdrawalStatus.PROCESSING]
        ).exists()

        if has_pending:
            raise WalletStateError(f"Cannot close Wallet #{wallet.id}: there are pending withdrawal operations.")

        prev_state = wallet.state
        wallet.state = WalletState.CLOSED
        wallet.save(update_fields=['state', 'updated_at'])

        WalletAuditLog.objects.create(
            wallet=wallet,
            actor=admin_user,
            previous_state=prev_state,
            new_state=WalletState.CLOSED,
            reason=reason.strip()
        )

        AdminAuditLog.objects.create(
            actor=admin_user,
            action='WALLET_CLOSE',
            target_model='Wallet',
            target_id=str(wallet.id),
            reason=reason.strip()
        )

        logger.info("Wallet #%s closed permanently by admin %s. Reason: %s", wallet.id, admin_user.username, reason)
        return wallet
