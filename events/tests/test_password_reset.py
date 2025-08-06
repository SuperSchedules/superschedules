from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core import mail
from rest_framework.test import APIClient


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordResetTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='resetuser',
            email='reset@example.com',
            password='initial-pass',
        )
        self.client = APIClient()

    def test_request_and_confirm_password_reset(self):
        resp = self.client.post('/api/v1/reset/', {'email': self.user.email}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['message'], 'Check your email for a password reset link.')
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        token = body.split('token=')[1].strip()
        new_pass = 'new-strong-pass'
        resp = self.client.post('/api/v1/reset/confirm/', {'token': token, 'password': new_pass}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['message'], 'Password has been reset.')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_pass))
