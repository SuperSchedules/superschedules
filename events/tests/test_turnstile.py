from unittest.mock import patch, Mock
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


class TurnstileVerificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.valid_payload = {
            "email": "test@example.com",
            "password": "secure-password-123",
            "first_name": "Test",
            "last_name": "User",
        }

    def test_registration_succeeds_when_turnstile_disabled(self):
        """Registration should succeed without Turnstile token when TURNSTILE_SECRET_KEY is not set."""
        # Default settings have empty TURNSTILE_SECRET_KEY
        resp = self.client.post("/api/v1/users", self.valid_payload, format="json")
        self.assertEqual(resp.status_code, 201)

    @override_settings(TURNSTILE_SECRET_KEY="test-secret-key")
    def test_registration_fails_without_token_when_turnstile_enabled(self):
        """Registration should fail when Turnstile is enabled but no token provided."""
        resp = self.client.post("/api/v1/users", self.valid_payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Security verification required", resp.json().get("detail", ""))

    @override_settings(TURNSTILE_SECRET_KEY="test-secret-key")
    @patch("api.views.requests.post")
    def test_registration_succeeds_with_valid_turnstile_token(self, mock_post):
        """Registration should succeed when Turnstile token is valid."""
        mock_response = Mock()
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        payload = {**self.valid_payload, "turnstileToken": "valid-token"}
        resp = self.client.post("/api/v1/users", payload, format="json")

        self.assertEqual(resp.status_code, 201)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertEqual(call_args[1]["data"]["response"], "valid-token")
        self.assertEqual(call_args[1]["data"]["secret"], "test-secret-key")

    @override_settings(TURNSTILE_SECRET_KEY="test-secret-key")
    @patch("api.views.requests.post")
    def test_registration_fails_with_invalid_turnstile_token(self, mock_post):
        """Registration should fail when Turnstile token is invalid."""
        mock_response = Mock()
        mock_response.json.return_value = {"success": False, "error-codes": ["invalid-input-response"]}
        mock_post.return_value = mock_response

        payload = {**self.valid_payload, "turnstileToken": "invalid-token"}
        resp = self.client.post("/api/v1/users", payload, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Security verification failed", resp.json().get("detail", ""))

    @override_settings(TURNSTILE_SECRET_KEY="test-secret-key")
    @patch("api.views.requests.post")
    def test_registration_fails_when_cloudflare_api_errors(self, mock_post):
        """Registration should fail when Cloudflare API call fails."""
        mock_post.side_effect = Exception("Network error")

        payload = {**self.valid_payload, "turnstileToken": "some-token"}
        resp = self.client.post("/api/v1/users", payload, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Security verification failed", resp.json().get("detail", ""))

    @override_settings(TURNSTILE_SECRET_KEY="test-secret-key")
    @patch("api.views.requests.post")
    def test_turnstile_verification_uses_correct_endpoint(self, mock_post):
        """Turnstile verification should call the correct Cloudflare endpoint."""
        mock_response = Mock()
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        payload = {**self.valid_payload, "turnstileToken": "test-token"}
        self.client.post("/api/v1/users", payload, format="json")

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertEqual(call_args[0][0], "https://challenges.cloudflare.com/turnstile/v0/siteverify")
        self.assertEqual(call_args[1]["timeout"], 10)
