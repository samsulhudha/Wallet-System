from rest_framework import generics, permissions
from drf_spectacular.utils import extend_schema
from .models import RiskDecision, RiskAction
from .serializers import RiskDecisionSerializer
from apps.accounts.permissions import IsAdminUserOnly


@extend_schema(
    tags=['Admin - Risk'],
    summary='List all flagged or blocked transactions requiring risk review',
    responses={200: RiskDecisionSerializer(many=True)}
)
class AdminFlaggedDecisionsListView(generics.ListAPIView):
    serializer_class = RiskDecisionSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminUserOnly]

    def get_queryset(self):
        return RiskDecision.objects.filter(
            decision__in=[RiskAction.FLAG, RiskAction.BLOCK]
        ).select_related('wallet', 'wallet__user').order_by('-created_at')

