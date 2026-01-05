from django.contrib import admin
from django.urls import path, include
from ninja import NinjaAPI
from api.views import router as api_router
from api.health import router as health_router
from locations.views import router as locations_router
from rest_framework_simplejwt.views import (TokenObtainPairView, TokenRefreshView)

# Import custom admin configuration to add build info to header
from config.admin import rag_tester_view

# Instantiate the API without a global authentication requirement.
# Individual routes will specify authentication as needed, allowing
# certain endpoints such as password reset to be accessed without
# credentials.
api = NinjaAPI()
api.add_router("/v1/", api_router)
api.add_router("/v1/locations", locations_router, tags=["locations"])
api.add_router("", health_router)


urlpatterns = [
    path("grappelli/", include("grappelli.urls")),
    path("admin/rag-tester/", rag_tester_view, name="admin_rag_tester"),
    path("admin/traces/api/", include("traces.urls")),  # Traces API before admin.site.urls
    path("admin/", admin.site.urls),
    # JWT token endpoints - no trailing slash (API convention)
    path("api/v1/token", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/token/refresh", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/", api.urls),
]

