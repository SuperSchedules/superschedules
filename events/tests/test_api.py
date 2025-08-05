from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from model_bakery import baker

from events.models import Source, Event


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
            "/api/token/", {"username": self.user.username, "password": self.password}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_events_requires_auth(self):
        resp = self.client.get("/api/v1/events")
        self.assertEqual(resp.status_code, 401)

    def test_event_date_filtering(self):
        self.authenticate()
        source = baker.make(Source)
        now = timezone.now()
        past_event = baker.make(Event, source=source, start_time=now - timedelta(days=1))
        future_event = baker.make(Event, source=source, start_time=now + timedelta(days=1))

        resp = self.client.get("/api/v1/events")
        ids = [ev["id"] for ev in resp.json()]
        self.assertIn(future_event.id, ids)
        self.assertNotIn(past_event.id, ids)

        start = (now - timedelta(days=2)).date().isoformat()
        end = (now + timedelta(days=2)).date().isoformat()
        resp = self.client.get("/api/v1/events", {"start": start, "end": end})
        ids = [ev["id"] for ev in resp.json()]
        self.assertIn(future_event.id, ids)
        self.assertIn(past_event.id, ids)
