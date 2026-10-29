from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field
from apps.wallets.models import Wallet, WalletState
from apps.common.money import SUPPORTED_CURRENCIES, minor_units_to_decimal
from apps.ledger.models import LedgerEntry


class WalletSerializer(serializers.ModelSerializer):
    available_balance_decimal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    reserved_balance_decimal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total_balance_decimal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    owner_username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Wallet
        fields = (
            'id', 'owner_username', 'currency', 'state',
            'available_balance', 'available_balance_decimal',
            'reserved_balance', 'reserved_balance_decimal',
            'total_balance_decimal', 'daily_limit',
            'created_at', 'updated_at'
        )
        read_only_fields = (
            'id', 'owner_username', 'state', 'available_balance',
            'available_balance_decimal', 'reserved_balance',
            'reserved_balance_decimal', 'total_balance_decimal',
            'created_at', 'updated_at'
        )


class CreateWalletSerializer(serializers.ModelSerializer):
    currency = serializers.ChoiceField(choices=sorted(SUPPORTED_CURRENCIES), default='INR')

    class Meta:
        model = Wallet
        fields = ('id', 'currency', 'daily_limit')
        read_only_fields = ('id',)

    def validate(self, attrs):
        user = self.context['request'].user
        currency = attrs.get('currency', 'INR')
        if Wallet.objects.filter(user=user, currency=currency).exists():
            raise serializers.ValidationError(
                f"You already possess a {currency} wallet. Only one wallet per user per currency is permitted."
            )
        return attrs

    def create(self, validated_data):
        user = self.context['request'].user
        return Wallet.objects.create(user=user, **validated_data)


class StateChangeRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, min_length=3, help_text="Mandatory reason for state transition")


class LedgerStatementEntrySerializer(serializers.ModelSerializer):
    transaction_id = serializers.IntegerField(source='transaction.id', read_only=True)
    reference_id = serializers.CharField(source='transaction.reference_id', read_only=True)
    transaction_type = serializers.CharField(source='transaction.transaction_type', read_only=True)
    account_type = serializers.CharField(source='account.account_type', read_only=True)
    currency = serializers.CharField(source='transaction.currency', read_only=True)
    amount_decimal = serializers.SerializerMethodField()
    description = serializers.CharField(source='transaction.description', read_only=True)
    posted_at = serializers.DateTimeField(source='transaction.posted_at', read_only=True)

    class Meta:
        model = LedgerEntry
        fields = (
            'id', 'transaction_id', 'reference_id', 'transaction_type',
            'account_type', 'direction', 'amount', 'amount_decimal',
            'currency', 'description', 'posted_at', 'created_at'
        )

    @extend_schema_field(serializers.DecimalField(max_digits=18, decimal_places=2))
    def get_amount_decimal(self, obj):
        return minor_units_to_decimal(obj.amount, obj.transaction.currency)
