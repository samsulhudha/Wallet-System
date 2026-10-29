from django.db import models
from django.conf import settings
from django.db.models import Q, CheckConstraint, UniqueConstraint
from apps.common.money import minor_units_to_decimal, SUPPORTED_CURRENCIES


class WalletState(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    FROZEN = 'FROZEN', 'Frozen'
    CLOSED = 'CLOSED', 'Closed'


class Wallet(models.Model):
    """
    Core customer wallet model holding available and reserved balances.
    All monetary amounts are stored as integer minor units (e.g. paise for INR).
    Guarantees one wallet per user per currency and non-negative balances via PostgreSQL constraints.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='wallets',
        help_text='Owner of this wallet'
    )
    currency = models.CharField(
        max_length=3,
        choices=[(c, c) for c in sorted(SUPPORTED_CURRENCIES)],
        default='INR',
        db_index=True,
        help_text='ISO 4217 Currency Code'
    )
    state = models.CharField(
        max_length=10,
        choices=WalletState.choices,
        default=WalletState.ACTIVE,
        db_index=True,
        help_text='Operational state of wallet'
    )
    available_balance = models.BigIntegerField(
        default=0,
        help_text='Funds immediately available for transfer or withdrawal (in minor units)'
    )
    reserved_balance = models.BigIntegerField(
        default=0,
        help_text='Funds locked pending settlement or bank callback (in minor units)'
    )
    daily_limit = models.BigIntegerField(
        null=True,
        blank=True,
        help_text='Custom daily outgoing limit in minor units (paise). If null, system default applies.'
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wallets'
        constraints = [
            UniqueConstraint(
                fields=['user', 'currency'],
                name='unique_user_currency_wallet'
            ),
            CheckConstraint(
                condition=Q(available_balance__gte=0),
                name='check_wallet_available_balance_non_negative'
            ),
            CheckConstraint(
                condition=Q(reserved_balance__gte=0),
                name='check_wallet_reserved_balance_non_negative'
            ),
        ]
        ordering = ['id']

    def __str__(self):
        return f"Wallet #{self.id} ({self.user.username} - {self.currency} - {self.state})"

    @property
    def total_balance(self) -> int:
        return self.available_balance + self.reserved_balance

    @property
    def available_balance_decimal(self):
        return minor_units_to_decimal(self.available_balance, self.currency)

    @property
    def reserved_balance_decimal(self):
        return minor_units_to_decimal(self.reserved_balance, self.currency)

    @property
    def total_balance_decimal(self):
        return minor_units_to_decimal(self.total_balance, self.currency)

    @property
    def is_active(self) -> bool:
        return self.state == WalletState.ACTIVE

    @property
    def is_frozen(self) -> bool:
        return self.state == WalletState.FROZEN

    @property
    def is_closed(self) -> bool:
        return self.state == WalletState.CLOSED

    def get_effective_daily_limit(self) -> int:
        if self.daily_limit is not None and self.daily_limit > 0:
            return self.daily_limit
        return getattr(settings, 'MAX_DAILY_TRANSFER_LIMIT_PAISE', 10000000)
