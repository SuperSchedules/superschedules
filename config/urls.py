from django.contrib import admin
from django.urls import include, path
from ninja import NinjaAPI
from ninja_jwt.authentication import JWTAuth
from api.views import router as api_router
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)


# Instantiate the API without default authentication so that
# non-authenticated endpoints can be explicitly declared.
api = NinjaAPI()
# Apply JWT authentication to all routes under the "/v1" prefix.
api.add_router("/v1/", api_router, auth=JWTAuth())


urlpatterns = [
    path("", include("events.urls")),
    path("admin/", admin.site.urls),
    path("api/v1/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/", api.urls),
]

