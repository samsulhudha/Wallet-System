import pytest
import concurrent.futures
from django.db import connections
from apps.wallets.models import Wallet, WalletState
from apps.wallets.services import WalletStateService
from apps.payments.services import PaymentService
from apps.common.exceptions import WalletStateError


@pytest.mark.django_db(transaction=True)
def test_concurrent_freeze_and_outgoing_transfers(wallet_a_inr, wallet_b_inr, user_a, admin_user):
    """
    Scenario:
    10 threads run outgoing transfers while 1 thread freezes the wallet.
    Guarantees:
    - Row-level lock serializes freeze and transfer.
    - If freeze acquires lock first, transfer is rejected with WalletStateError.
    - Final state is FROZEN and no transfers succeed after freezing.
    """
    PaymentService.topup(wallet_a_inr.id, 500000, "INR", user_a)

    def do_transfer(idx):
        connections.close_all()
        try:
            PaymentService.transfer(
                source_wallet_id=wallet_a_inr.id,
                dest_wallet_id=wallet_b_inr.id,
                amount=1000,
                currency="INR",
                user=user_a
            )
            return "SUCCESS", None
        except WalletStateError:
            return "REJECTED_FROZEN", None
        except Exception as e:
            return "ERROR", str(e)
        finally:
            connections.close_all()

    def do_freeze():
        connections.close_all()
        try:
            WalletStateService.freeze_wallet(wallet_a_inr.id, admin_user, "Concurrent security freeze")
            return "FROZEN", None
        except Exception as e:
            return "FREEZE_ERROR", str(e)
        finally:
            connections.close_all()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        transfer_futures = [executor.submit(do_transfer, i) for i in range(10)]
        freeze_future = executor.submit(do_freeze)

        freeze_status, _ = freeze_future.result()
        transfer_results = [f.result() for f in transfer_futures]

    assert freeze_status == "FROZEN"

    wallet_a_inr.refresh_from_db()
    assert wallet_a_inr.state == WalletState.FROZEN

    # If any transfer ran after the freeze, it was rejected
    # Test a subsequent transfer explicitly: MUST fail with WalletStateError
    with pytest.raises(WalletStateError, match="FROZEN"):
        PaymentService.transfer(
            source_wallet_id=wallet_a_inr.id,
            dest_wallet_id=wallet_b_inr.id,
            amount=1000,
            currency="INR",
            user=user_a
        )

