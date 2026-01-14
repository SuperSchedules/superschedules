from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from model_bakery import baker

from events.models import Event, ServiceToken
from venues.models import Venue


class EventDetailTests(TestCase):
    def setUp(self):
        self.jwt_client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(username="detailuser", password="pass")
        refresh = RefreshToken.for_user(self.user)
        self.jwt_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")

        self.svc_client = APIClient()
        self.svc_token = baker.make(ServiceToken)
        self.svc_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.svc_token.token}")

        self.venue = baker.make(Venue, name="Test Venue", city="Newton", state="MA")
        self.event = baker.make(Event, venue=self.venue)

    def test_get_event_with_jwt(self):
        resp = self.jwt_client.get(f"/api/v1/events/{self.event.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == self.event.id

    def test_get_event_with_service_token(self):
        resp = self.svc_client.get(f"/api/v1/events/{self.event.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == self.event.id

    def test_get_event_404(self):
        resp = self.jwt_client.get("/api/v1/events/999999")
        assert resp.status_code == 404

