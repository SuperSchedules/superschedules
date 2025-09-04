from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

from chat_service.app import app


class FastAPIServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="fa-user@example.com", email="fa-user@example.com", password="pass1234"
        )
        refresh = RefreshToken.for_user(self.user)
        self.jwt = str(refresh.access_token)
        self.client = TestClient(app)

    def test_health_check(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "chat_service"

    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_stream_chat_single_model(self, mock_get_llm, mock_get_events):
        # Relevant events for context (minimal)
        mock_get_events.return_value = [
            {"id": 1, "title": "E1", "description": "d", "location": "l", "start_time": None, "end_time": None}
        ]

        # Mock LLM streaming to yield two tokens then a final done chunk
        async def fake_stream(**kwargs):
            yield {"token": "Hello", "done": False}
            yield {"token": " world", "done": False}
            yield {"token": "", "done": True, "response_time_ms": 42, "success": True}

        mock_service = mock_get_llm.return_value
        mock_service.generate_streaming_response = fake_stream

        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "hi", "single_model_mode": True}

        # Stream the response and collect SSE lines
        chunks = []
        with self.client.stream("POST", "/chat/stream", json=payload, headers=headers) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    chunks.append(line[len("data: "):])

        # Expect model A tokens (single model mode uses primary model A) and a final SYSTEM marker
        assert any('"model": "A"' in c and '"done": false' in c for c in chunks)
        assert any('"model": "A"' in c and '"done": true' in c for c in chunks)
        assert any('"model": "SYSTEM"' in c and '"done": true' in c for c in chunks)
