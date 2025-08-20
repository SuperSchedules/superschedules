from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from model_bakery import baker

from events.models import Source, SiteStrategy


class SourceAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.password = "strong-pass"
        self.user = baker.make(User, username="sourceuser")
        self.user.set_password(self.password)
        self.user.save()
        self.client = APIClient()

    def authenticate(self):
        resp = self.client.post(
            "/api/v1/token/", {"username": self.user.username, "password": self.password}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_sources_requires_auth(self):
        resp = self.client.get("/api/v1/sources/")
        self.assertEqual(resp.status_code, 401)

    def test_list_and_create_sources(self):
        self.authenticate()
        other_user = baker.make(get_user_model())
        baker.make(Source, user=self.user, name="Mine", base_url="https://example.com")
        baker.make(Source, user=other_user, name="Other", base_url="https://other.com")

        resp = self.client.get("/api/v1/sources/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "Mine")
        self.assertIn("date_added", data[0])
        self.assertIn("last_run_at", data[0])

        payload = {"base_url": "https://new.com", "name": "New Source"}
        resp = self.client.post("/api/v1/sources/", payload, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["status"], "not_run")
        self.assertIn("date_added", resp.json())
        self.assertIn("last_run_at", resp.json())
        self.assertIsNone(resp.json()["last_run_at"])
        self.assertTrue(
            Source.objects.filter(base_url="https://new.com", user=self.user).exists()
        )

    def test_create_source_links_strategy(self):
        self.authenticate()
        SiteStrategy.objects.create(domain="link.com")
        payload = {"base_url": "https://link.com", "name": "Link"}
        resp = self.client.post("/api/v1/sources/", payload, format="json")
        self.assertEqual(resp.status_code, 201)
        source = Source.objects.get(id=resp.json()["id"])
        strategy = SiteStrategy.objects.get(domain="link.com")
        self.assertEqual(source.site_strategy, strategy)
