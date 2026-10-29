from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    # OpenAPI 3 Schema & Swagger UI
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # Direct admin flags route (before Django Admin site catches it)
    path('', include('apps.risk.urls')),

    path('admin/', admin.site.urls),

    # Authentication
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/', include('apps.accounts.urls')),

    # Domain routes with /api/ prefix
    path('api/', include('apps.wallets.urls')),
    path('api/', include('apps.payments.urls')),
    path('api/', include('apps.risk.urls')),

    # Root route aliases for core spec (e.g. /wallets/{id}, /transfers/)
    path('', include('apps.wallets.urls')),
    path('', include('apps.payments.urls')),
]

