from django.contrib import admin
from django.urls import path
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
    path("admin/", admin.site.urls),
    path("api/v1/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/", api.urls),
]

