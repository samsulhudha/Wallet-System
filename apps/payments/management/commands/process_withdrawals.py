import time
import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from apps.payments.models import WithdrawalJob, WithdrawalJobStatus, WithdrawalStatus
from apps.payments.mock_bank import MockBankService
from apps.payments.services import PaymentService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Processes asynchronous outbox withdrawal jobs via durable PostgreSQL queue.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Process pending jobs once and exit immediately (useful in tests/batch scripts).'
        )
        parser.add_argument(
            '--interval',
            type=float,
            default=2.0,
            help='Polling interval in seconds when running continuously.'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,
            help='Maximum number of jobs to claim per batch.'
        )

    def handle(self, *args, **options):
        once = options['once']
        interval = options['interval']
        batch_size = options['batch_size']

        self.stdout.write(self.style.SUCCESS("Starting durable withdrawal job processor..."))

        while True:
            processed = self.process_batch(batch_size)
            if once:
                self.stdout.write(f"Completed single pass. Processed {processed} jobs.")
                break
            time.sleep(interval)

    def process_batch(self, batch_size: int) -> int:
        processed_count = 0
        now = timezone.now()

        # Claim jobs safely using PostgreSQL SELECT FOR UPDATE SKIP LOCKED
        with transaction.atomic():
            jobs = list(
                WithdrawalJob.objects.select_for_update(skip_locked=True)
                .filter(
                    status__in=[WithdrawalJobStatus.PENDING, WithdrawalJobStatus.PROCESSING],
                    next_attempt_at__lte=now
                )
                .select_related('withdrawal', 'withdrawal__wallet')
                [:batch_size]
            )

            if not jobs:
                return 0

            for job in jobs:
                job.status = WithdrawalJobStatus.PROCESSING
                job.attempts += 1
                job.save(update_fields=['status', 'attempts', 'updated_at'])

        # Process claimed jobs
        for job in jobs:
            try:
                self.process_job(job)
                processed_count += 1
            except Exception as e:
                logger.exception("Error processing withdrawal job #%s: %s", job.id, e)
                with transaction.atomic():
                    job.refresh_from_db()
                    job.last_error = str(e)
                    if job.attempts >= job.max_attempts:
                        job.status = WithdrawalJobStatus.FAILED
                    else:
                        job.status = WithdrawalJobStatus.PENDING
                        job.next_attempt_at = timezone.now() + timedelta(seconds=2 ** job.attempts)
                    job.save(update_fields=['status', 'last_error', 'next_attempt_at', 'updated_at'])

        return processed_count

    def process_job(self, job: WithdrawalJob):
        withdrawal = job.withdrawal
        simulated_outcome = job.last_error if job.last_error in ("SUCCESS", "REJECTION", "TIMEOUT") else "SUCCESS"

        outcome, callback_payload = MockBankService.process_bank_dispatch(
            withdrawal=withdrawal,
            simulated_outcome=simulated_outcome
        )

        if outcome == "TIMEOUT":
            # Reschedule or mark needs reconciliation without releasing reserved funds
            with transaction.atomic():
                job.refresh_from_db()
                if job.attempts >= job.max_attempts:
                    job.status = WithdrawalJobStatus.NEEDS_RECONCILIATION
                    job.last_error = "Exceeded max attempts due to continuous mock bank timeouts."
                else:
                    job.status = WithdrawalJobStatus.PENDING
                    job.next_attempt_at = timezone.now() + timedelta(seconds=2 * job.attempts)
                    job.last_error = "Timeout connecting to mock bank gateway; will retry."
                job.save(update_fields=['status', 'next_attempt_at', 'last_error', 'updated_at'])
            self.stdout.write(
                self.style.WARNING(
                    f"Job #{job.id} for Withdrawal #{withdrawal.id} encountered TIMEOUT. "
                    f"Funds remain safely reserved."
                )
            )
            return

        # Bank dispatched: verify and feed callback
        signature = MockBankService.generate_signature(callback_payload)
        success, msg = PaymentService.process_bank_callback(callback_payload, signature)

        self.stdout.write(
            self.style.SUCCESS(
                f"Job #{job.id} for Withdrawal #{withdrawal.id} processed ({outcome}). Result: {msg}"
            )
        )
