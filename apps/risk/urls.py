from django.urls import path
from .views import AdminFlaggedDecisionsListView

urlpatterns = [
    path('admin/flags/', AdminFlaggedDecisionsListView.as_view(), name='admin_flags'),
    path('admin/risk/flagged/', AdminFlaggedDecisionsListView.as_view(), name='admin_risk_flagged'),
]
