from ninja.security import HttpBearer
from events.models import ServiceToken


class ServiceTokenAuth(HttpBearer):
    def authenticate(self, request, token):
        try:
            return ServiceToken.objects.get(token=token)
        except ServiceToken.DoesNotExist:
            return None
