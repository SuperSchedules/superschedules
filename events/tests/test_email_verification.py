from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailVerificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_registration_sends_verification_email(self):
        """Registration should send a verification email."""
        payload = {
            "email": "newuser@example.com",
            "password": "strong-pass-123",
            "first_name": "New",
            "last_name": "User",
        }
        resp = self.client.post("/api/v1/users", payload, format="json")
        self.assertEqual(resp.status_code, 201)

        # Check that an email was sent
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["newuser@example.com"])
        self.assertIn("Verify Your EventZombie Account", email.subject)
        self.assertIn("/verify-email?token=", email.body)

    def test_verify_email_activates_user(self):
        """Valid verification token should activate the user."""
        # Create inactive user
        user = User.objects.create_user(
            username="test@example.com",
            email="test@example.com",
            password="test-pass",
            is_active=False,
        )
        self.assertFalse(user.is_active)

        # Generate token
        token = signing.dumps({"user_id": user.id}, salt="email-verification")

        # Verify
        resp = self.client.post(f"/api/v1/users/verify/{token}", format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Email verified successfully", resp.json()["message"])

        # Check user is now active
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_verify_email_allows_login_after(self):
        """After verification, user should be able to log in."""
        # Create inactive user
        user = User.objects.create_user(
            username="test@example.com",
            email="test@example.com",
            password="test-pass-123",
            is_active=False,
        )

        # Cannot login before verification
        login_resp = self.client.post(
            "/api/v1/token",
            {"username": "test@example.com", "password": "test-pass-123"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 401)

        # Verify email
        token = signing.dumps({"user_id": user.id}, salt="email-verification")
        self.client.post(f"/api/v1/users/verify/{token}", format="json")

        # Now login should work
        login_resp = self.client.post(
            "/api/v1/token",
            {"username": "test@example.com", "password": "test-pass-123"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 200)
        self.assertIn("access", login_resp.json())

    def test_verify_invalid_token_returns_error(self):
        """Invalid token should return 400 error."""
        resp = self.client.post("/api/v1/users/verify/invalid-token", format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid verification link", resp.json()["message"])

    @override_settings(EMAIL_VERIFICATION_TIMEOUT=1)
    def test_verify_expired_token_returns_error(self):
        """Expired token should return 400 with helpful message."""
        import time

        user = User.objects.create_user(
            username="test@example.com",
            email="test@example.com",
            password="test-pass",
            is_active=False,
        )

        token = signing.dumps({"user_id": user.id}, salt="email-verification")

        # Wait for token to expire (1 second timeout)
        time.sleep(2)

        resp = self.client.post(f"/api/v1/users/verify/{token}", format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("expired", resp.json()["message"].lower())

    def test_verify_already_active_user(self):
        """Verifying an already active user should return success message."""
        user = User.objects.create_user(
            username="test@example.com",
            email="test@example.com",
            password="test-pass",
            is_active=True,  # Already active
        )

        token = signing.dumps({"user_id": user.id}, salt="email-verification")
        resp = self.client.post(f"/api/v1/users/verify/{token}", format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("already verified", resp.json()["message"].lower())

    def test_resend_verification_email(self):
        """Resend endpoint should send new verification email."""
        # Create inactive user
        User.objects.create_user(
            username="test@example.com",
            email="test@example.com",
            password="test-pass",
            is_active=False,
        )

        resp = self.client.post(
            "/api/v1/users/resend-verification",
            {"email": "test@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        # Check email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["test@example.com"])

    def test_resend_verification_nonexistent_email(self):
        """Resend for non-existent email should return success (prevent enumeration)."""
        resp = self.client.post(
            "/api/v1/users/resend-verification",
            {"email": "nobody@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        # No email should be sent
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_verification_active_user(self):
        """Resend for already active user should return success but not send email."""
        User.objects.create_user(
            username="test@example.com",
            email="test@example.com",
            password="test-pass",
            is_active=True,  # Already active
        )

        resp = self.client.post(
            "/api/v1/users/resend-verification",
            {"email": "test@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        # No email should be sent (user is already active)
        self.assertEqual(len(mail.outbox), 0)

    def test_verification_token_for_deleted_user(self):
        """Token for deleted user should return error."""
        user = User.objects.create_user(
            username="test@example.com",
            email="test@example.com",
            password="test-pass",
            is_active=False,
        )
        token = signing.dumps({"user_id": user.id}, salt="email-verification")

        # Delete the user
        user.delete()

        resp = self.client.post(f"/api/v1/users/verify/{token}", format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid", resp.json()["message"])

    def test_full_registration_verification_flow(self):
        """Test the complete flow: register -> verify -> login."""
        # 1. Register
        payload = {
            "email": "fullflow@example.com",
            "password": "secure-password-123",
            "first_name": "Full",
            "last_name": "Flow",
        }
        resp = self.client.post("/api/v1/users", payload, format="json")
        self.assertEqual(resp.status_code, 201)

        # 2. Extract token from email
        self.assertEqual(len(mail.outbox), 1)
        email_body = mail.outbox[0].body
        # Extract token from URL in email body
        import re
        match = re.search(r'/verify-email\?token=([^\s]+)', email_body)
        self.assertIsNotNone(match, "Token not found in email body")
        token = match.group(1)

        # 3. Verify email
        resp = self.client.post(f"/api/v1/users/verify/{token}", format="json")
        self.assertEqual(resp.status_code, 200)

        # 4. Login
        login_resp = self.client.post(
            "/api/v1/token",
            {"username": "fullflow@example.com", "password": "secure-password-123"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 200)
        self.assertIn("access", login_resp.json())
