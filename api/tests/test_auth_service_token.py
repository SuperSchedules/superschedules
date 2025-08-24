from django.test import TestCase
from model_bakery import baker

from api.auth import ServiceTokenAuth
from events.models import ServiceToken


class ServiceTokenAuthTests(TestCase):
    def test_authenticate_valid_and_invalid(self):
        token_obj = baker.make(ServiceToken)
        auth = ServiceTokenAuth()

        class Req:
            pass

        # Valid
        user_or_token = auth.authenticate(Req(), token_obj.token)
        assert user_or_token == token_obj

        # Invalid
        none_user = auth.authenticate(Req(), "not-a-token")
        assert none_user is None

