from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from .models import Wallet
from .serializers import (
    WalletSerializer,
    CreateWalletSerializer,
    StateChangeRequestSerializer,
    LedgerStatementEntrySerializer,
)
from .services import WalletStateService
from apps.accounts.permissions import IsWalletOwnerOrAdmin, IsAdminUserOnly
from apps.ledger.models import LedgerEntry
from apps.common.pagination import StandardResultsSetPagination


@extend_schema(
    tags=['Wallets'],
    summary='List wallets or create a new currency wallet',
)
class WalletListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return CreateWalletSerializer
        return WalletSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet = serializer.save()
        return Response(WalletSerializer(wallet).data, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        if self.request.user.is_staff:
            return Wallet.objects.all().select_related('user').order_by('id')
        return Wallet.objects.filter(user=self.request.user).order_by('id')


@extend_schema(
    tags=['Wallets'],
    summary='Retrieve authorized wallet balance and status',
    responses={200: WalletSerializer}
)
class WalletDetailView(generics.RetrieveAPIView):
    serializer_class = WalletSerializer
    permission_classes = [permissions.IsAuthenticated, IsWalletOwnerOrAdmin]
    queryset = Wallet.objects.all()


@extend_schema(
    tags=['Statements'],
    summary='Retrieve ledger-backed transaction statement for a wallet',
    parameters=[
        OpenApiParameter(name='start_date', description='Filter from ISO datetime', required=False, type=str),
        OpenApiParameter(name='end_date', description='Filter to ISO datetime', required=False, type=str),
        OpenApiParameter(name='transaction_type', description='Filter by transaction type (TOPUP, TRANSFER, etc.)', required=False, type=str),
    ],
    responses={200: LedgerStatementEntrySerializer(many=True)}
)
class WalletTransactionsStatementView(generics.ListAPIView):
    serializer_class = LedgerStatementEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsWalletOwnerOrAdmin]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        wallet_id = self.kwargs['pk']
        wallet = get_object_or_404(Wallet, id=wallet_id)
        self.check_object_permissions(self.request, wallet)

        qs = LedgerEntry.objects.filter(
            account__wallet=wallet
        ).select_related('transaction', 'account').order_by('-transaction__posted_at', '-id')

        start_date = self.request.query_params.get('start_date')
        if start_date:
            parsed_start = parse_datetime(start_date.replace(' ', '+'))
            if parsed_start:
                qs = qs.filter(transaction__posted_at__gte=parsed_start)

        end_date = self.request.query_params.get('end_date')
        if end_date:
            parsed_end = parse_datetime(end_date.replace(' ', '+'))
            if parsed_end:
                qs = qs.filter(transaction__posted_at__lte=parsed_end)

        tx_type = self.request.query_params.get('transaction_type')
        if tx_type:
            qs = qs.filter(transaction__transaction_type=tx_type.upper().strip())

        return qs


@extend_schema(
    tags=['Admin - Wallets'],
    summary='Administratively freeze a wallet',
    request=StateChangeRequestSerializer,
    responses={200: WalletSerializer}
)
class AdminFreezeWalletView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminUserOnly]

    def post(self, request, pk):
        serializer = StateChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet = WalletStateService.freeze_wallet(
            wallet_id=pk,
            admin_user=request.user,
            reason=serializer.validated_data['reason']
        )
        return Response(WalletSerializer(wallet).data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Admin - Wallets'],
    summary='Administratively unfreeze a wallet',
    request=StateChangeRequestSerializer,
    responses={200: WalletSerializer}
)
class AdminUnfreezeWalletView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminUserOnly]

    def post(self, request, pk):
        serializer = StateChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet = WalletStateService.unfreeze_wallet(
            wallet_id=pk,
            admin_user=request.user,
            reason=serializer.validated_data['reason']
        )
        return Response(WalletSerializer(wallet).data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Admin - Wallets'],
    summary='Administratively permanently close a wallet',
    request=StateChangeRequestSerializer,
    responses={200: WalletSerializer}
)
class AdminCloseWalletView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminUserOnly]

    def post(self, request, pk):
        serializer = StateChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet = WalletStateService.close_wallet(
            wallet_id=pk,
            admin_user=request.user,
            reason=serializer.validated_data['reason']
        )
        return Response(WalletSerializer(wallet).data, status=status.HTTP_200_OK)
