import logging
from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse

from .models import Withdrawal, Transfer
from .serializers import (
    TopUpRequestSerializer,
    TopUpResponseSerializer,
    TransferRequestSerializer,
    TransferResponseSerializer,
    WithdrawalRequestSerializer,
    WithdrawalResponseSerializer,
    TransferReversalRequestSerializer,
    BankCallbackPayloadSerializer,
)
from .services import PaymentService
from .idempotency import IdempotencyManager
from apps.accounts.permissions import IsWalletOwnerOrAdmin, IsAdminUserOnly
from apps.wallets.models import Wallet

logger = logging.getLogger(__name__)


@extend_schema(
    tags=['Top-ups'],
    summary='Fund a wallet via mock payment gateway (Requires Idempotency-Key header)',
    parameters=[
        OpenApiParameter(name='Idempotency-Key', description='Unique client idempotency token', required=True, type=str, location=OpenApiParameter.HEADER)
    ],
    request=TopUpRequestSerializer,
    responses={
        201: TopUpResponseSerializer,
        400: OpenApiResponse(description='Validation error or wallet closed'),
        409: OpenApiResponse(description='Idempotency conflict'),
    }
)
class TopUpView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        idempotency_key = IdempotencyManager.get_idempotency_key(request)
        endpoint = f"POST /api/wallets/{pk}/topups/"

        serializer = TopUpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        record, is_new = IdempotencyManager.acquire(
            user=request.user,
            key=idempotency_key,
            endpoint=endpoint,
            payload=request.data
        )

        if not is_new:
            return Response(record.response_data, status=record.response_code)

        try:
            topup_obj = PaymentService.topup(
                wallet_id=pk,
                amount=serializer.validated_data['amount_minor_units'],
                currency=serializer.validated_data['currency'],
                user=request.user,
                provider_outcome=serializer.validated_data.get('mock_provider_outcome', 'SUCCESS'),
            )
            response_data = TopUpResponseSerializer(topup_obj).data
            status_code = status.HTTP_201_CREATED if topup_obj.status == 'COMPLETED' else status.HTTP_400_BAD_REQUEST

            IdempotencyManager.resolve(record, status_code, response_data, resource_id=topup_obj.id)
            return Response(response_data, status=status_code)
        except Exception as e:
            IdempotencyManager.fail(record, {'error': str(e)})
            raise


@extend_schema(
    tags=['Transfers'],
    summary='Execute an atomic P2P transfer between two wallets (Requires Idempotency-Key header)',
    parameters=[
        OpenApiParameter(name='Idempotency-Key', description='Unique client idempotency token', required=True, type=str, location=OpenApiParameter.HEADER)
    ],
    request=TransferRequestSerializer,
    responses={
        201: TransferResponseSerializer,
        400: OpenApiResponse(description='Insufficient balance, daily limit exceeded, or validation error'),
        403: OpenApiResponse(description='Fraud risk rule blocked transfer'),
        409: OpenApiResponse(description='Idempotency key collision with different payload'),
    }
)
class TransferView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        idempotency_key = IdempotencyManager.get_idempotency_key(request)
        endpoint = "POST /api/transfers/"

        serializer = TransferRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        record, is_new = IdempotencyManager.acquire(
            user=request.user,
            key=idempotency_key,
            endpoint=endpoint,
            payload=request.data
        )

        if not is_new:
            return Response(record.response_data, status=record.response_code)

        try:
            transfer_obj = PaymentService.transfer(
                source_wallet_id=serializer.validated_data['source_wallet_id'],
                dest_wallet_id=serializer.validated_data['destination_wallet_id'],
                amount=serializer.validated_data['amount_minor_units'],
                currency=serializer.validated_data['currency'],
                user=request.user,
                description=serializer.validated_data.get('description', '')
            )
            response_data = TransferResponseSerializer(transfer_obj).data
            status_code = status.HTTP_201_CREATED

            IdempotencyManager.resolve(record, status_code, response_data, resource_id=transfer_obj.id)
            return Response(response_data, status=status_code)
        except Exception as e:
            IdempotencyManager.fail(record, {'error': str(e)})
            raise


@extend_schema(
    tags=['Withdrawals'],
    summary='Initiate an asynchronous bank withdrawal (Requires Idempotency-Key header)',
    parameters=[
        OpenApiParameter(name='Idempotency-Key', description='Unique client idempotency token', required=True, type=str, location=OpenApiParameter.HEADER)
    ],
    request=WithdrawalRequestSerializer,
    responses={
        202: WithdrawalResponseSerializer,
        400: OpenApiResponse(description='Insufficient funds or invalid state'),
        409: OpenApiResponse(description='Idempotency conflict'),
    }
)
class WithdrawalRequestView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        idempotency_key = IdempotencyManager.get_idempotency_key(request)
        endpoint = f"POST /api/wallets/{pk}/withdrawals/"

        serializer = WithdrawalRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        record, is_new = IdempotencyManager.acquire(
            user=request.user,
            key=idempotency_key,
            endpoint=endpoint,
            payload=request.data
        )

        if not is_new:
            # On idempotency retry for withdrawal, return latest withdrawal state!
            if record.resource_id:
                withdrawal = Withdrawal.objects.filter(id=record.resource_id).first()
                if withdrawal:
                    return Response(WithdrawalResponseSerializer(withdrawal).data, status=record.response_code)
            return Response(record.response_data, status=record.response_code)

        try:
            withdrawal_obj = PaymentService.request_withdrawal(
                wallet_id=pk,
                amount=serializer.validated_data['amount_minor_units'],
                currency=serializer.validated_data['currency'],
                user=request.user,
                bank_account_info=serializer.validated_data.get('bank_account_info', ''),
                simulated_outcome=serializer.validated_data.get('simulated_outcome', 'SUCCESS'),
            )
            response_data = WithdrawalResponseSerializer(withdrawal_obj).data
            status_code = status.HTTP_202_ACCEPTED

            IdempotencyManager.resolve(record, status_code, response_data, resource_id=withdrawal_obj.id)
            return Response(response_data, status=status_code)
        except Exception as e:
            IdempotencyManager.fail(record, {'error': str(e)})
            raise


@extend_schema(
    tags=['Withdrawals'],
    summary='Retrieve current status of a withdrawal resource',
    responses={200: WithdrawalResponseSerializer}
)
class WithdrawalDetailView(generics.RetrieveAPIView):
    serializer_class = WithdrawalResponseSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Withdrawal.objects.all()

    def get_object(self):
        withdrawal = super().get_object()
        if not (self.request.user.is_staff or withdrawal.wallet.user_id == self.request.user.id):
            self.permission_denied(self.request, message="You do not have permission to view this withdrawal.")
        return withdrawal


@extend_schema(
    tags=['Withdrawals'],
    summary='Dedicated mock bank callback webhook endpoint',
    parameters=[
        OpenApiParameter(name='X-Bank-Signature', description='HMAC SHA-256 signature of JSON payload', required=True, type=str, location=OpenApiParameter.HEADER)
    ],
    request=BankCallbackPayloadSerializer,
    responses={
        200: OpenApiResponse(description='Callback processed or duplicate ignored'),
        400: OpenApiResponse(description='Invalid signature or malformed payload'),
    }
)
class BankCallbackWebhookView(APIView):
    permission_classes = [permissions.AllowAny]  # Signature verified by HMAC header
    serializer_class = BankCallbackPayloadSerializer

    def post(self, request):
        signature = request.headers.get('X-Bank-Signature') or request.META.get('HTTP_X_BANK_SIGNATURE', '')
        success, message = PaymentService.process_bank_callback(request.data, signature)

        if not success:
            return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'status': 'acknowledged', 'message': message}, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Admin - Reversals'],
    summary='Administratively reverse a completed transfer using compensating ledger entries',
    parameters=[
        OpenApiParameter(name='Idempotency-Key', description='Unique client idempotency token', required=True, type=str, location=OpenApiParameter.HEADER)
    ],
    request=TransferReversalRequestSerializer,
    responses={
        200: TransferResponseSerializer,
        400: OpenApiResponse(description='Transfer cannot be reversed or recipient has insufficient funds'),
        403: OpenApiResponse(description='Staff permission required'),
    }
)
class AdminTransferReversalView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminUserOnly]

    def post(self, request, pk):
        idempotency_key = IdempotencyManager.get_idempotency_key(request)
        endpoint = f"POST /api/admin/transfers/{pk}/reverse/"

        serializer = TransferReversalRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        record, is_new = IdempotencyManager.acquire(
            user=request.user,
            key=idempotency_key,
            endpoint=endpoint,
            payload=request.data
        )

        if not is_new:
            return Response(record.response_data, status=record.response_code)

        try:
            transfer_obj = PaymentService.reverse_transfer(
                transfer_id=pk,
                admin_user=request.user,
                reason=serializer.validated_data['reason']
            )
            response_data = TransferResponseSerializer(transfer_obj).data
            status_code = status.HTTP_200_OK

            IdempotencyManager.resolve(record, status_code, response_data, resource_id=transfer_obj.id)
            return Response(response_data, status=status_code)
        except Exception as e:
            IdempotencyManager.fail(record, {'error': str(e)})
            raise
