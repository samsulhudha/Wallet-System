from django.urls import path
from .views import AdminFlaggedDecisionsListView

urlpatterns = [
    path('admin/risk/flagged/', AdminFlaggedDecisionsListView.as_view(), name='admin_risk_flagged'),
]
