from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from django.core import mail


class PasswordResetEdgeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='reset-edge', email='edge@example.com', password='old-pass'
        )
        self.client = APIClient()

    def test_invalid_token_returns_400(self):
        resp = self.client.post('/api/v1/reset/confirm/', {'token': 'not-a-valid', 'password': 'x'}, format='json')
        assert resp.status_code == 400
        assert 'Invalid or expired token' in resp.json()['message']

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_expired_token_returns_400(self):
        # Request reset to get a token
        resp = self.client.post('/api/v1/reset/', {'email': self.user.email}, format='json')
        assert resp.status_code == 200
        token = mail.outbox[0].body.split('token=')[1].strip()

        # Using a zero-timeout will force immediate expiry
        with override_settings(PASSWORD_RESET_TIMEOUT=0):
            resp = self.client.post('/api/v1/reset/confirm/', {'token': token, 'password': 'new-pass'}, format='json')
            assert resp.status_code == 400
            assert 'Invalid or expired token' in resp.json()['message']

