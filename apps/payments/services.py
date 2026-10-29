import logging
import uuid
from datetime import timedelta
from typing import Tuple, Dict, Any, Optional
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.db.models import Sum

from apps.accounts.models import User
from apps.wallets.models import Wallet, WalletState
from apps.ledger.services import LedgerService
from apps.risk.engine import RiskEngine
from apps.audit.models import AdminAuditLog
from .models import (
    TopUp,
    TopUpStatus,
    Transfer,
    TransferStatus,
    Withdrawal,
    WithdrawalStatus,
    WithdrawalJob,
    WithdrawalJobStatus,
    BankCallbackLog,
)
from .mock_bank import MockBankService
from apps.common.exceptions import (
    DomainError,
    InsufficientFundsError,
    WalletStateError,
    SelfTransferError,
    CurrencyMismatchError,
    DailyLimitExceededError,
    InvalidStateTransitionError,
    ReversalNotEligibleError,
)

logger = logging.getLogger(__name__)


class PaymentService:
    """
    Authoritative domain service for all money-moving operations.
    Maintains row-level locks, transactional integrity, and atomic double-entry postings.
    """

    @classmethod
    def calculate_rolling_daily_outgoing(cls, wallet: Wallet) -> int:
        """
        Calculates total outgoing volume within the rolling 24-hour window.
        Includes completed transfers and requested/processing/completed withdrawals.
        """
        window_start = timezone.now() - timedelta(hours=24)

        recent_transfers = Transfer.objects.filter(
            source_wallet=wallet,
            status=TransferStatus.COMPLETED,
            created_at__gte=window_start
        ).aggregate(total=Sum('amount'))['total'] or 0

        recent_withdrawals = Withdrawal.objects.filter(
            wallet=wallet,
            status__in=[WithdrawalStatus.REQUESTED, WithdrawalStatus.PROCESSING, WithdrawalStatus.COMPLETED],
            created_at__gte=window_start
        ).aggregate(total=Sum('amount'))['total'] or 0

        return recent_transfers + recent_withdrawals

    @classmethod
    @transaction.atomic
    def topup(
        cls,
        wallet_id: int,
        amount: int,
        currency: str,
        user: User,
        provider_outcome: str = "SUCCESS",
        description: str = ""
    ) -> TopUp:
        """
        Processes a wallet funding request.
        """
        wallet = Wallet.objects.select_for_update().get(id=wallet_id)

        # Authorization: owner or staff
        if not (user.is_staff or wallet.user_id == user.id):
            raise DomainError("You do not have permission to top-up this wallet.", status_code=403)

        if wallet.is_closed:
            raise WalletStateError(f"Cannot top-up closed wallet #{wallet.id}.")

        if wallet.currency != currency:
            raise CurrencyMismatchError(
                f"Requested currency '{currency}' does not match wallet currency '{wallet.currency}'."
            )

        provider_ref = f"topup_{uuid.uuid4().hex}"

        if provider_outcome.upper() == "SUCCESS":
            topup_obj = TopUp.objects.create(
                wallet=wallet,
                amount=amount,
                currency=currency,
                provider_reference=provider_ref,
                status=TopUpStatus.COMPLETED,
                completed_at=timezone.now()
            )

            # Atomic ledger posting
            LedgerService.record_topup(
                wallet=wallet,
                amount=amount,
                reference_id=str(topup_obj.id),
                description=description or f"Top-up of {amount} {currency} on Wallet #{wallet.id}"
            )

            # Increment balance
            wallet.available_balance += amount
            wallet.save(update_fields=['available_balance', 'updated_at'])

            logger.info("Top-up #%s succeeded: credited %s %s to Wallet #%s", topup_obj.id, amount, currency, wallet.id)
            return topup_obj
        else:
            topup_obj = TopUp.objects.create(
                wallet=wallet,
                amount=amount,
                currency=currency,
                provider_reference=provider_ref,
                status=TopUpStatus.FAILED,
                error_message="Payment provider rejected top-up charge."
            )
            logger.warning("Top-up #%s failed via mock provider for Wallet #%s", topup_obj.id, wallet.id)
            return topup_obj

    @classmethod
    @transaction.atomic
    def transfer(
        cls,
        source_wallet_id: int,
        dest_wallet_id: int,
        amount: int,
        currency: str,
        user: User,
        description: str = ""
    ) -> Transfer:
        """
        Executes an atomic P2P transfer between two wallets.
        Enforces:
            1. Deterministic ascending primary-key row-level locks to prevent deadlocks.
            2. Source wallet state ACTIVE.
            3. Destination wallet state NOT CLOSED.
            4. Available funds >= amount.
            5. Rolling 24-hour daily outgoing limits.
            6. Pluggable fraud detection rules.
            7. Atomic balance mutation and balanced ledger entries.
        """
        if source_wallet_id == dest_wallet_id:
            raise SelfTransferError("Transfers between the same wallet are prohibited.")

        # Lock wallets in strict ascending ID order
        ordered_ids = sorted([source_wallet_id, dest_wallet_id])
        locked_wallets = {
            w.id: w
            for w in Wallet.objects.filter(id__in=ordered_ids).select_for_update().order_by('id')
        }

        if len(locked_wallets) != 2:
            raise DomainError("One or both wallets could not be found.")

        source = locked_wallets[source_wallet_id]
        dest = locked_wallets[dest_wallet_id]

        # Authorization: owner or staff
        if not (user.is_staff or source.user_id == user.id):
            raise DomainError("You do not have permission to transfer from this wallet.", status_code=403)

        # Currency validation
        if source.currency != currency or dest.currency != currency:
            raise CurrencyMismatchError(
                f"Transfer currency mismatch. Source: {source.currency}, Dest: {dest.currency}, Requested: {currency}"
            )

        # State validation
        if not source.is_active:
            raise WalletStateError(
                f"Source wallet #{source.id} is {source.state}. Outgoing transfers are not allowed."
            )

        if dest.is_closed:
            raise WalletStateError(f"Destination wallet #{dest.id} is closed and cannot receive transfers.")

        # Funds availability
        if source.available_balance < amount:
            raise InsufficientFundsError(
                f"Insufficient funds in wallet #{source.id}. Available: {source.available_balance}, Requested: {amount}"
            )

        # Daily limit check
        daily_limit = source.get_effective_daily_limit()
        rolling_outgoing = cls.calculate_rolling_daily_outgoing(source)
        if rolling_outgoing + amount > daily_limit:
            raise DailyLimitExceededError(
                f"Transfer of {amount} exceeds 24-hour outgoing limit of {daily_limit}. "
                f"Current 24h outgoing: {rolling_outgoing}."
            )

        # Pluggable Fraud Rule Engine
        RiskEngine.evaluate_transfer(wallet=source, amount=amount, currency=currency)

        # Mutate balances atomically
        source.available_balance -= amount
        dest.available_balance += amount
        source.save(update_fields=['available_balance', 'updated_at'])
        dest.save(update_fields=['available_balance', 'updated_at'])

        transfer_obj = Transfer.objects.create(
            source_wallet=source,
            destination_wallet=dest,
            amount=amount,
            currency=currency,
            status=TransferStatus.COMPLETED,
            description=description,
        )

        # Atomic double-entry ledger posting
        LedgerService.record_transfer(
            source_wallet=source,
            dest_wallet=dest,
            amount=amount,
            reference_id=str(transfer_obj.id),
            description=f"Transfer #{transfer_obj.id} from #{source.id} to #{dest.id}"
        )

        logger.info(
            "Transfer #%s complete: %s %s from Wallet #%s to #%s",
            transfer_obj.id, amount, currency, source.id, dest.id
        )
        return transfer_obj

    @classmethod
    @transaction.atomic
    def request_withdrawal(
        cls,
        wallet_id: int,
        amount: int,
        currency: str,
        user: User,
        bank_account_info: str = "",
        simulated_outcome: str = "SUCCESS"
    ) -> Withdrawal:
        """
        Initiates an asynchronous bank withdrawal.
        Immediately reserves funds and enqueues durable outbox job.
        """
        wallet = Wallet.objects.select_for_update().get(id=wallet_id)

        if not (user.is_staff or wallet.user_id == user.id):
            raise DomainError("You do not have permission to withdraw from this wallet.", status_code=403)

        if not wallet.is_active:
            raise WalletStateError(f"Wallet #{wallet.id} is {wallet.state}. Withdrawals are disallowed.")

        if wallet.currency != currency:
            raise CurrencyMismatchError(f"Currency '{currency}' does not match wallet currency '{wallet.currency}'.")

        if wallet.available_balance < amount:
            raise InsufficientFundsError(
                f"Insufficient funds for withdrawal. Available: {wallet.available_balance}, Requested: {amount}"
            )

        daily_limit = wallet.get_effective_daily_limit()
        rolling_outgoing = cls.calculate_rolling_daily_outgoing(wallet)
        if rolling_outgoing + amount > daily_limit:
            raise DailyLimitExceededError(
                f"Withdrawal of {amount} exceeds 24-hour limit of {daily_limit}. Recent outgoing: {rolling_outgoing}."
            )

        # Risk Engine evaluation
        RiskEngine.evaluate_withdrawal(wallet=wallet, amount=amount, currency=currency)

        # Reserve funds: Available -> Reserved
        wallet.available_balance -= amount
        wallet.reserved_balance += amount
        wallet.save(update_fields=['available_balance', 'reserved_balance', 'updated_at'])

        provider_ref = f"wdr_{uuid.uuid4().hex}"

        withdrawal = Withdrawal.objects.create(
            wallet=wallet,
            amount=amount,
            currency=currency,
            provider_reference=provider_ref,
            bank_account_info=bank_account_info,
            status=WithdrawalStatus.REQUESTED,
        )

        # Post ledger reservation
        LedgerService.record_withdrawal_reserve(
            wallet=wallet,
            amount=amount,
            reference_id=str(withdrawal.id),
            description=f"Reservation for Withdrawal #{withdrawal.id}"
        )

        # Create durable outbox job
        WithdrawalJob.objects.create(
            withdrawal=withdrawal,
            status=WithdrawalJobStatus.PENDING,
            last_error=simulated_outcome,  # Pass configured outcome to mock worker
        )

        logger.info(
            "Withdrawal #%s requested (ref %s): %s %s reserved on Wallet #%s",
            withdrawal.id, provider_ref, amount, currency, wallet.id
        )
        return withdrawal

    @classmethod
    def process_bank_callback(cls, payload: Dict[str, Any], signature: str) -> Tuple[bool, str]:
        """
        Receives and processes bank callbacks/webhooks idempotently and securely.
        Uses HMAC signature verification, callback deduplication, and row locking.
        """
        if not MockBankService.verify_signature(payload, signature):
            logger.warning("Bank callback rejected: invalid HMAC signature.")
            return False, "Invalid signature"

        event_id = payload.get('provider_event_id')
        provider_ref = payload.get('provider_reference')
        event_type = payload.get('event_type')

        if not event_id or not provider_ref:
            return False, "Missing event_id or provider_reference"

        # Deduplication via database uniqueness
        try:
            with transaction.atomic():
                BankCallbackLog.objects.create(
                    provider_event_id=event_id,
                    provider_reference=provider_ref,
                    event_type=event_type,
                    payload=payload
                )
        except IntegrityError:
            logger.info("Duplicate callback event %s already processed. Ignoring.", event_id)
            return True, "Duplicate event ignored"

        # Process withdrawal state transition
        with transaction.atomic():
            withdrawal = Withdrawal.objects.select_for_update().filter(
                provider_reference=provider_ref
            ).first()

            if not withdrawal:
                logger.error("Callback reference %s not found in withdrawals", provider_ref)
                return False, "Withdrawal not found"

            # Late or redundant callbacks
            if withdrawal.status in (WithdrawalStatus.COMPLETED, WithdrawalStatus.FAILED):
                logger.info(
                    "Withdrawal #%s is already in final state %s. Late callback %s ignored.",
                    withdrawal.id, withdrawal.status, event_id
                )
                return True, "Withdrawal already finalized"

            wallet = Wallet.objects.select_for_update().get(id=withdrawal.wallet_id)

            if event_type == "PAYOUT_COMPLETED":
                # Clear reserved balance
                wallet.reserved_balance -= withdrawal.amount
                wallet.save(update_fields=['reserved_balance', 'updated_at'])

                # Record ledger settlement
                LedgerService.record_withdrawal_settle(
                    wallet=wallet,
                    amount=withdrawal.amount,
                    reference_id=str(withdrawal.id),
                    description=f"Bank settlement for Withdrawal #{withdrawal.id}"
                )

                withdrawal.status = WithdrawalStatus.COMPLETED
                withdrawal.save(update_fields=['status', 'updated_at'])

                WithdrawalJob.objects.filter(withdrawal=withdrawal).update(
                    status=WithdrawalJobStatus.COMPLETED
                )

                logger.info("Withdrawal #%s finalized as COMPLETED via bank callback", withdrawal.id)

            elif event_type == "PAYOUT_REJECTED":
                # Refund reserved balance to available balance exactly once
                wallet.reserved_balance -= withdrawal.amount
                wallet.available_balance += withdrawal.amount
                wallet.save(update_fields=['reserved_balance', 'available_balance', 'updated_at'])

                # Record compensating ledger refund
                LedgerService.record_withdrawal_refund(
                    wallet=wallet,
                    amount=withdrawal.amount,
                    reference_id=str(withdrawal.id),
                    description=f"Bank rejection refund for Withdrawal #{withdrawal.id}"
                )

                withdrawal.status = WithdrawalStatus.FAILED
                withdrawal.failure_reason = payload.get('failure_reason', 'Bank payout rejected')
                withdrawal.save(update_fields=['status', 'failure_reason', 'updated_at'])

                WithdrawalJob.objects.filter(withdrawal=withdrawal).update(
                    status=WithdrawalJobStatus.FAILED,
                    last_error=withdrawal.failure_reason
                )

                logger.info("Withdrawal #%s refunded as FAILED via bank callback", withdrawal.id)

            else:
                logger.warning("Unrecognized bank callback event type: %s", event_type)
                return False, f"Unknown event_type: {event_type}"

        return True, "Callback processed successfully"

    @classmethod
    @transaction.atomic
    def reverse_transfer(
        cls,
        transfer_id: int,
        admin_user: User,
        reason: str
    ) -> Transfer:
        """
        Administrative reversal of a completed transfer.
        Accounting:
            - DEBIT: Destination Wallet Available (Liability -)
            - CREDIT: Source Wallet Available (Liability +)
        Policy:
            If recipient has insufficient available funds to reverse,
            the reversal is rejected to prevent negative balances and uphold non-negative invariant.
        """
        if not admin_user.is_staff:
            raise DomainError("Only administrators can reverse transfers.", status_code=403)

        if not reason or not reason.strip():
            raise DomainError("A mandatory administrative reason is required to reverse a transfer.")

        transfer_obj = Transfer.objects.select_for_update().filter(id=transfer_id).first()
        if not transfer_obj:
            raise DomainError(f"Transfer #{transfer_id} not found.")

        if transfer_obj.is_reversed or transfer_obj.status == TransferStatus.REVERSED:
            raise ReversalNotEligibleError(f"Transfer #{transfer_id} has already been reversed.")

        if transfer_obj.status != TransferStatus.COMPLETED:
            raise ReversalNotEligibleError(f"Only completed transfers can be reversed (status={transfer_obj.status}).")

        # Deterministic locking of both wallets
        ordered_ids = sorted([transfer_obj.source_wallet_id, transfer_obj.destination_wallet_id])
        wallets = {
            w.id: w
            for w in Wallet.objects.filter(id__in=ordered_ids).select_for_update().order_by('id')
        }

        source = wallets[transfer_obj.source_wallet_id]
        dest = wallets[transfer_obj.destination_wallet_id]

        # Policy enforcement: Recipient available funds check
        if dest.available_balance < transfer_obj.amount:
            raise InsufficientFundsError(
                f"Cannot reverse Transfer #{transfer_id}: recipient wallet #{dest.id} has insufficient "
                f"available balance ({dest.available_balance}) to recover {transfer_obj.amount}."
            )

        # Mutate balances
        dest.available_balance -= transfer_obj.amount
        source.available_balance += transfer_obj.amount
        dest.save(update_fields=['available_balance', 'updated_at'])
        source.save(update_fields=['available_balance', 'updated_at'])

        transfer_obj.is_reversed = True
        transfer_obj.status = TransferStatus.REVERSED
        transfer_obj.save(update_fields=['is_reversed', 'status'])

        # Post compensating ledger transaction
        LedgerService.record_transfer_reversal(
            source_wallet=source,
            dest_wallet=dest,
            amount=transfer_obj.amount,
            reference_id=str(transfer_obj.id),
            description=f"Administrative reversal of Transfer #{transfer_obj.id}: {reason.strip()}"
        )

        # Record admin audit log
        AdminAuditLog.objects.create(
            actor=admin_user,
            action='TRANSFER_REVERSAL',
            target_model='Transfer',
            target_id=str(transfer_obj.id),
            reason=reason.strip(),
            metadata={
                'source_wallet_id': source.id,
                'dest_wallet_id': dest.id,
                'amount': transfer_obj.amount,
                'currency': transfer_obj.currency,
            }
        )

        logger.info(
            "Transfer #%s reversed by admin %s. Reason: %s",
            transfer_obj.id, admin_user.username, reason
        )
        return transfer_obj

