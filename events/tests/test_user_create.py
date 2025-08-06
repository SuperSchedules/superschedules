from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


class UserCreationTests(TestCase):
    def test_create_user_and_cannot_login_before_verification(self):
        client = APIClient()
        payload = {
            "email": "newuser@example.com",
            "password": "strong-pass",
            "first_name": "New",
            "last_name": "User",
        }
        resp = client.post("/api/v1/users/", payload, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["email"], payload["email"])

        User = get_user_model()
        user = User.objects.get(username=payload["email"])
        self.assertFalse(user.is_active)

        login_resp = client.post(
            "/api/v1/token/",
            {"username": payload["email"], "password": payload["password"]},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 401)
