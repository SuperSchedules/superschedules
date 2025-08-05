from django.contrib import admin
from django.urls import include, path
from ninja import NinjaAPI
from ninja_jwt.authentication import JWTAuth
from api.views import router as api_router
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)


api = NinjaAPI(auth=JWTAuth())
api.add_router("/v1/", api_router)


urlpatterns = [
    path("", include("events.urls")),
    path("admin/", admin.site.urls),
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/", api.urls),
]

