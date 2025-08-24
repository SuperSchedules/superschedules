from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from model_bakery import baker

from events.models import SiteStrategy


class StrategyPutTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = baker.make(User, username="putuser")
        self.password = "pass1234"
        self.user.set_password(self.password)
        self.user.save()
        self.client = APIClient()
        resp = self.client.post(
            "/api/v1/token/",
            {"username": self.user.username, "password": self.password},
            format="json",
        )
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_put_updates_fields_without_success_side_effects(self):
        domain = "put-example.com"
        SiteStrategy.objects.create(domain=domain, best_selectors=[".old"])  # existing
        payload = {"best_selectors": [".new"], "notes": "updated", "success": True}
        resp = self.client.put(f"/api/v1/sites/{domain}/strategy", payload, format="json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["best_selectors"] == [".new"]
        assert data["notes"] == "updated"
        # success should be ignored in PUT handler
        assert data["total_attempts"] == 0
        assert data["successful_attempts"] == 0

