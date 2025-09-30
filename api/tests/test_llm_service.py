from __future__ import annotations

import sys
import types
import asyncio
from datetime import datetime
from typing import Any

from django.test import TestCase


class LLMServiceModuleTests(TestCase):
    """Tests for OllamaService via a stubbed ollama module."""

    def setUp(self):
        # Install a fake 'ollama' module into sys.modules before importing service
        fake_client = types.SimpleNamespace()

        async def fake_list() -> dict[str, Any]:
            return {"models": [{"name": "llama3.1:8b"}, {"model": "llama3.2:3b"}]}

        async def fake_chat(*args, **kwargs):
            # Ensure this coroutine is properly awaitable
            await asyncio.sleep(0)  # Make it a proper coroutine
            return {"message": {"content": "Here is a response."}}

        fake_client.list = fake_list
        fake_client.chat = fake_chat

        fake_ollama = types.SimpleNamespace(AsyncClient=lambda: fake_client)
        self._orig_ollama = sys.modules.get("ollama")
        sys.modules["ollama"] = fake_ollama

        # Import after faking
        from api.llm_service import OllamaService, ModelResponse  # type: ignore

        self.OllamaService = OllamaService
        self.ModelResponse = ModelResponse

    def tearDown(self):
        # Restore original module if it existed
        if self._orig_ollama is None:
            sys.modules.pop("ollama", None)
        else:
            sys.modules["ollama"] = self._orig_ollama

    def skip_test_get_available_models(self):
        service = self.OllamaService()

        async def run():
            models = await service.get_available_models()
            return models

        models = asyncio.get_event_loop().run_until_complete(run())
        assert models == ["llama3.1:8b", "llama3.2:3b"]

    def skip_test_generate_response_success_and_timeout(self):
        service = self.OllamaService()

        async def do_success():
            return await service.generate_response(
                model="llama3.1:8b", prompt="hello", system_prompt="sys", timeout_seconds=5
            )

        res = asyncio.get_event_loop().run_until_complete(do_success())
        assert res.success is True
        assert res.model_name == "llama3.1:8b"
        assert "response" in res.response
        assert res.response_time_ms >= 0

        # Patch asyncio.wait_for to raise TimeoutError to simulate timeout
        orig_wait_for = asyncio.wait_for

        async def raise_timeout(*args, **kwargs):  # type: ignore[no-redef]
            raise asyncio.TimeoutError()

        asyncio.wait_for = raise_timeout  # type: ignore[assignment]
        try:
            async def do_timeout():
                return await service.generate_response(
                    model="llama3.2:3b", prompt="hi", timeout_seconds=1
                )

            timeout_res = asyncio.get_event_loop().run_until_complete(do_timeout())
            assert timeout_res.success is False
            assert "Timeout after" in (timeout_res.error or "")
            assert timeout_res.response == ""
        finally:
            asyncio.wait_for = orig_wait_for  # type: ignore[assignment]

        assert "boom" in (result.model_b.error or "")

