from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import patch, AsyncMock

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from fastapi.testclient import TestClient

from chat_service.app import app


class FastAPIDualStreamTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="fa2-user@example.com", email="fa2-user@example.com", password="pass1234"
        )
        refresh = RefreshToken.for_user(self.user)
        self.jwt = str(refresh.access_token)
        self.client = TestClient(app)

    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_stream_chat_dual_models(self, mock_get_llm, mock_get_events):
        mock_get_events.return_value = [
            {"id": 1, "title": "E1", "description": "d", "location": "l", "start_time": None, "end_time": None}
        ]

        # Build a fake service that yields two token chunks then a final done
        class FakeService:
            DEFAULT_MODEL_A = "modelA"
            DEFAULT_MODEL_B = "modelB"

            async def generate_streaming_response(self, model: str, prompt: str, system_prompt: str | None = None):
                # type: (str, str, str | None) -> AsyncGenerator[dict[str, Any], None]
                async def gen():
                    yield {"message": {"content": "X"}, "done": False, "token": "Hello"}
                    yield {"message": {"content": "Y"}, "done": False, "token": " there"}
                    yield {"message": {"content": ""}, "done": True, "response_time_ms": 21, "success": True}

                # The real impl yields dicts directly; adapt to that shape
                async for chunk in gen():
                    # stream_model_response expects dict with keys as below
                    if not chunk["done"]:
                        yield {"message": {"content": chunk["token"]}, "done": False, "token": chunk["token"]}
                    else:
                        yield {"message": {"content": ""}, "done": True, "response_time_ms": 21, "success": True}

        mock_get_llm.return_value = FakeService()

        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "hi", "single_model_mode": False}

        chunks = []
        with self.client.stream("POST", "/chat/stream", json=payload, headers=headers) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    chunks.append(line[len("data: "):])

        # Expect both A and B streams and a final SYSTEM marker
        assert any('"model": "A"' in c and '"done": false' in c for c in chunks)
        assert any('"model": "A"' in c and '"done": true' in c for c in chunks)
        assert any('"model": "B"' in c and '"done": false' in c for c in chunks)
        assert any('"model": "B"' in c and '"done": true' in c for c in chunks)
        assert any('"model": "SYSTEM"' in c and '"done": true' in c for c in chunks)
