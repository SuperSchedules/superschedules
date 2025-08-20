from rest_framework.test import APIClient
from django.test import TestCase


class HealthEndpointsTests(TestCase):
    def test_live_endpoint(self):
        client = APIClient()
        resp = client.get('/api/live')
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_ready_endpoint(self):
        client = APIClient()
        resp = client.get('/api/ready')
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pass"
        assert "db" in data["checks"]
        assert "cache" in data["checks"]
