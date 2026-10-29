import uuid
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.accounts.models import User
from apps.wallets.models import Wallet, WalletState
from apps.payments.services import PaymentService
from apps.risk.models import RiskRuleConfig
from apps.ledger.reconciliation import ReconciliationService


class Command(BaseCommand):
    help = "Seed database with demo users, funded wallets, double-entry ledger entries, and fraud rules"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding demo data for Wallet Service..."))

        with transaction.atomic():
            # 1. Admin User
            admin_user, created = User.objects.get_or_create(
                username="admin",
                defaults={
                    "email": "admin@wallet.local",
                    "first_name": "System",
                    "last_name": "Admin",
                    "is_staff": True,
                    "is_superuser": True,
                }
            )
            if created or not admin_user.check_password("AdminPassword123!"):
                admin_user.set_password("AdminPassword123!")
                admin_user.is_staff = True
                admin_user.is_superuser = True
                admin_user.save()
            self.stdout.write(self.style.SUCCESS("[OK] Admin user created: admin@wallet.local / AdminPassword123!"))

            # 2. Customer Alice
            alice, created = User.objects.get_or_create(
                username="alice",
                defaults={
                    "email": "alice@wallet.local",
                    "first_name": "Alice",
                    "last_name": "Smith",
                }
            )
            if created or not alice.check_password("AlicePassword123!"):
                alice.set_password("AlicePassword123!")
                alice.save()

            alice_wallet, created = Wallet.objects.get_or_create(
                user=alice,
                currency="INR",
                defaults={"state": WalletState.ACTIVE}
            )
            if alice_wallet.available_balance == 0:
                PaymentService.topup(
                    wallet_id=alice_wallet.id,
                    amount=500000,  # 5,000.00 INR (500,000 paise)
                    currency="INR",
                    user=alice,
                    description="Initial seed demo funding for Alice",
                )
            self.stdout.write(self.style.SUCCESS("[OK] Alice user created: alice@wallet.local / AlicePassword123! (Balance: 5,000.00 INR)"))

            # 3. Customer Bob
            bob, created = User.objects.get_or_create(
                username="bob",
                defaults={
                    "email": "bob@wallet.local",
                    "first_name": "Bob",
                    "last_name": "Jones",
                }
            )
            if created or not bob.check_password("BobPassword123!"):
                bob.set_password("BobPassword123!")
                bob.save()

            bob_wallet, created = Wallet.objects.get_or_create(
                user=bob,
                currency="INR",
                defaults={"state": WalletState.ACTIVE}
            )
            if bob_wallet.available_balance == 0:
                PaymentService.topup(
                    wallet_id=bob_wallet.id,
                    amount=100000,  # 1,000.00 INR (100,000 paise)
                    currency="INR",
                    user=bob,
                    description="Initial seed demo funding for Bob",
                )
            self.stdout.write(self.style.SUCCESS("[OK] Bob user created: bob@wallet.local / BobPassword123! (Balance: 1,000.00 INR)"))

            # 4. Default Risk Rules
            RiskRuleConfig.objects.get_or_create(
                rule_id="rule_velocity_transfers",
                defaults={
                    "name": "Velocity Check: Max Transfers in Window",
                    "is_enabled": True,
                    "parameters": {"max_transfers": 5, "window_minutes": 5},
                    "description": "Block transfers when exceeding 5 transfers in 5 minutes",
                }
            )
            RiskRuleConfig.objects.get_or_create(
                rule_id="rule_account_age_limit",
                defaults={
                    "name": "Account Age Limit on High Value Transfers",
                    "is_enabled": True,
                    "parameters": {"max_amount_paise": 5000000, "min_age_days": 7},
                    "description": "Flag transfers > 50,000 INR on accounts younger than 7 days",
                }
            )
            RiskRuleConfig.objects.get_or_create(
                rule_id="rule_rapid_withdrawal",
                defaults={
                    "name": "Rapid Withdrawal After Large Top-up",
                    "is_enabled": True,
                    "parameters": {"min_topup_paise": 1000000, "window_minutes": 30},
                    "description": "Flag withdrawals within 30 min of a large top-up (> 10,000 INR)",
                }
            )
            self.stdout.write(self.style.SUCCESS("[OK] Default fraud & velocity rules configured."))

        # 5. Ledger Reconciliation Check
        alice_rec = ReconciliationService.reconcile_wallet(alice_wallet)
        bob_rec = ReconciliationService.reconcile_wallet(bob_wallet)
        if alice_rec.is_balanced and bob_rec.is_balanced:
            self.stdout.write(self.style.SUCCESS("[OK] Ledger reconciliation verified: 100% balance integrity across all accounts."))
        else:
            self.stdout.write(self.style.ERROR("[WARN] Discrepancy detected during initial seed reconciliation!"))
