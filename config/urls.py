from django.contrib import admin
from django.urls import path, include
from ninja import NinjaAPI
from api.views import router as api_router
from rest_framework_simplejwt.views import (TokenObtainPairView, TokenRefreshView)


# Instantiate the API without a global authentication requirement.
# Individual routes will specify authentication as needed, allowing
# certain endpoints such as password reset to be accessed without
# credentials.
api = NinjaAPI()
api.add_router("/v1/", api_router)


urlpatterns = [
    path("grappelli/", include("grappelli.urls")),
    path("admin/", admin.site.urls),
    path("api/v1/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/", api.urls),
]

