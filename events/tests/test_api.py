from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from model_bakery import baker

from events.models import Source, Event, ServiceToken


class EventAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.password = "strong-pass"
        self.user = baker.make(User, username="apiuser")
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

    def test_events_requires_auth(self):
        resp = self.client.get("/api/v1/events/")
        self.assertEqual(resp.status_code, 401)

    def test_event_date_filtering(self):
        self.authenticate()
        source = baker.make(Source)
        now = timezone.now()
        past_event = baker.make(Event, source=source, start_time=now - timedelta(days=1))
        future_event = baker.make(Event, source=source, start_time=now + timedelta(days=1))

        resp = self.client.get("/api/v1/events/")
        ids = [ev["id"] for ev in resp.json()]
        self.assertIn(future_event.id, ids)
        self.assertNotIn(past_event.id, ids)

        start = (now - timedelta(days=2)).date().isoformat()
        end = (now + timedelta(days=2)).date().isoformat()
        resp = self.client.get("/api/v1/events/", {"start": start, "end": end})
        ids = [ev["id"] for ev in resp.json()]
        self.assertIn(future_event.id, ids)
        self.assertIn(past_event.id, ids)


class EventCRUDTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = baker.make(ServiceToken)

    def auth_service(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token.token}")

    def test_jwt_cannot_create_event(self):
        User = get_user_model()
        password = "strong-pass"
        user = baker.make(User, username="jwtuser")
        user.set_password(password)
        user.save()
        client = APIClient()
        resp = client.post(
            "/api/v1/token/", {"username": user.username, "password": password}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        jwt = resp.data["access"]
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {jwt}")
        source = baker.make(Source)
        payload = {
            "source_id": source.id,
            "external_id": "ext1",
            "title": "Ev",
            "description": "Desc",
            "location": "Loc",
            "start_time": timezone.now().isoformat(),
        }
        resp = client.post("/api/v1/events/", payload, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_service_token_full_crud(self):
        self.auth_service()
        source = baker.make(Source)
        payload = {
            "source_id": source.id,
            "external_id": "ext1",
            "title": "Ev",
            "description": "Desc",
            "location": "Loc",
            "start_time": timezone.now().isoformat(),
        }
        resp = self.client.post("/api/v1/events/", payload, format="json")
        self.assertEqual(resp.status_code, 201)
        event_id = resp.json()["id"]

        resp = self.client.put(
            f"/api/v1/events/{event_id}", {"title": "New"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], "New")

        resp = self.client.delete(f"/api/v1/events/{event_id}")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Event.objects.filter(id=event_id).exists())
