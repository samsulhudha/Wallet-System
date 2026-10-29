from rest_framework import serializers
from apps.common.money import decimal_to_minor_units, SUPPORTED_CURRENCIES, MoneyValidationError
from .models import TopUp, Transfer, Withdrawal, WithdrawalStatus, TransferStatus


class TopUpRequestSerializer(serializers.Serializer):
    amount = serializers.CharField(required=True, help_text="Amount in standard currency decimal string, e.g. '100.00'")
    currency = serializers.ChoiceField(choices=sorted(SUPPORTED_CURRENCIES), default='INR')
    mock_provider_outcome = serializers.ChoiceField(choices=['SUCCESS', 'FAILURE'], default='SUCCESS', required=False)

    def validate(self, attrs):
        try:
            attrs['amount_minor_units'] = decimal_to_minor_units(attrs['amount'], attrs['currency'])
        except MoneyValidationError as e:
            raise serializers.ValidationError({"amount": str(e)})
        return attrs


class TopUpResponseSerializer(serializers.ModelSerializer):
    amount_decimal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = TopUp
        fields = (
            'id', 'wallet', 'amount', 'amount_decimal', 'currency',
            'provider_reference', 'status', 'error_message',
            'created_at', 'completed_at'
        )


class TransferRequestSerializer(serializers.Serializer):
    source_wallet_id = serializers.IntegerField(required=True)
    destination_wallet_id = serializers.IntegerField(required=True)
    amount = serializers.CharField(required=True, help_text="Amount in decimal string format, e.g. '100.00'")
    currency = serializers.ChoiceField(choices=sorted(SUPPORTED_CURRENCIES), default='INR')
    description = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        if attrs['source_wallet_id'] == attrs['destination_wallet_id']:
            raise serializers.ValidationError({"destination_wallet_id": "Cannot transfer funds to the same wallet."})
        try:
            attrs['amount_minor_units'] = decimal_to_minor_units(attrs['amount'], attrs['currency'])
        except MoneyValidationError as e:
            raise serializers.ValidationError({"amount": str(e)})
        return attrs


class TransferResponseSerializer(serializers.ModelSerializer):
    amount_decimal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = Transfer
        fields = (
            'id', 'source_wallet', 'destination_wallet', 'amount',
            'amount_decimal', 'currency', 'status', 'is_reversed',
            'description', 'created_at'
        )


class WithdrawalRequestSerializer(serializers.Serializer):
    amount = serializers.CharField(required=True, help_text="Amount in decimal format, e.g. '50.00'")
    currency = serializers.ChoiceField(choices=sorted(SUPPORTED_CURRENCIES), default='INR')
    bank_account_info = serializers.CharField(required=False, default='', allow_blank=True)
    simulated_outcome = serializers.ChoiceField(
        choices=['SUCCESS', 'REJECTION', 'TIMEOUT'],
        default='SUCCESS',
        required=False,
        help_text="Configurable outcome for mock bank integration"
    )

    def validate(self, attrs):
        try:
            attrs['amount_minor_units'] = decimal_to_minor_units(attrs['amount'], attrs['currency'])
        except MoneyValidationError as e:
            raise serializers.ValidationError({"amount": str(e)})
        return attrs


class WithdrawalResponseSerializer(serializers.ModelSerializer):
    amount_decimal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = Withdrawal
        fields = (
            'id', 'wallet', 'amount', 'amount_decimal', 'currency',
            'provider_reference', 'status', 'bank_account_info',
            'failure_reason', 'created_at', 'updated_at'
        )


class TransferReversalRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, min_length=3, help_text="Mandatory reason for transfer reversal")


class BankCallbackPayloadSerializer(serializers.Serializer):
    provider_event_id = serializers.CharField(required=True, help_text="Unique event token from bank")
    provider_reference = serializers.CharField(required=True, help_text="Withdrawal transaction provider reference")
    event_type = serializers.ChoiceField(choices=['PAYOUT_COMPLETED', 'PAYOUT_REJECTED'], required=True)
    amount = serializers.IntegerField(required=False)
    currency = serializers.CharField(required=False)
    failure_reason = serializers.CharField(required=False, allow_blank=True, default='')

