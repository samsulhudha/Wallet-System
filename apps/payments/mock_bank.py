import hmac
import hashlib
import json
import logging
import uuid
from typing import Dict, Any, Tuple
from django.conf import settings
from django.utils import timezone
from .models import Withdrawal, WithdrawalStatus, BankCallbackLog

logger = logging.getLogger(__name__)


class MockBankError(Exception):
    pass


class MockBankService:
    """
    Simulates an external banking network interface with configurable asynchronous outcomes.
    Supports HMAC signature verification, callback deduplication, and timeout handling.
    """

    @classmethod
    def generate_signature(cls, payload: Dict[str, Any]) -> str:
        secret = getattr(settings, 'BANK_WEBHOOK_SECRET', 'super-secret-bank-callback-key-2026')
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return hmac.new(secret.encode('utf-8'), serialized.encode('utf-8'), hashlib.sha256).hexdigest()

    @classmethod
    def verify_signature(cls, payload: Dict[str, Any], signature: str) -> bool:
        if not signature:
            return False
        expected = cls.generate_signature(payload)
        return hmac.compare_digest(expected, signature)

    @classmethod
    def process_bank_dispatch(cls, withdrawal: Withdrawal, simulated_outcome: str = "SUCCESS") -> Tuple[str, Dict[str, Any]]:
        """
        Simulates dispatching a payout instruction to the external banking partner.
        Outcomes:
            - SUCCESS: Bank accepts and will trigger success callback.
            - REJECTION: Bank immediately or asynchronously rejects.
            - TIMEOUT: External gateway times out without definitive answer.
        """
        logger.info(
            "Dispatching withdrawal %s (ref %s) to mock bank. Configured outcome: %s",
            withdrawal.id, withdrawal.provider_reference, simulated_outcome
        )

        event_id = f"evt_{uuid.uuid4().hex[:16]}"

        if simulated_outcome == "TIMEOUT":
            logger.warning("Mock bank simulated TIMEOUT for ref %s", withdrawal.provider_reference)
            return "TIMEOUT", {
                "error": "Gateway timeout connecting to core banking system",
                "provider_reference": withdrawal.provider_reference
            }

        status_event = "PAYOUT_COMPLETED" if simulated_outcome == "SUCCESS" else "PAYOUT_REJECTED"
        failure_reason = "Insufficient bank liquidity or invalid account details" if simulated_outcome != "SUCCESS" else ""

        callback_payload = {
            "provider_event_id": event_id,
            "provider_reference": withdrawal.provider_reference,
            "event_type": status_event,
            "amount": withdrawal.amount,
            "currency": withdrawal.currency,
            "failure_reason": failure_reason,
            "timestamp": timezone.now().isoformat(),
        }

        return "DISPATCHED", callback_payload

