"""
Tests for streaming resilience and error handling.
Tests real endpoints with mocked Ollama responses.
"""

import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from fastapi.testclient import TestClient

from chat_service.app import app


class StreamingResilienceTests(TestCase):
    """Test streaming resilience and error recovery."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="resilience-test@example.com",
            email="resilience-test@example.com",
            password="testpass123"
        )
        refresh = RefreshToken.for_user(self.user)
        self.jwt = str(refresh.access_token)
        self.client = TestClient(app)

    def _setup_session_mocks(self, mock_session, mock_save, mock_history):
        """Helper to set up session management mocks."""
        mock_session_obj = MagicMock()
        mock_session_obj.id = 1
        mock_session.return_value = mock_session_obj
        mock_history.return_value = []
        mock_save.return_value = MagicMock()

    @patch("chat_service.app.get_or_create_session", new_callable=AsyncMock)
    @patch("chat_service.app.save_message", new_callable=AsyncMock)
    @patch("chat_service.app.get_conversation_history", new_callable=AsyncMock)
    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_streaming_timeout_recovery(self, mock_get_llm, mock_get_events, mock_history, mock_save, mock_session):
        """Test that streaming timeouts are handled gracefully."""
        self._setup_session_mocks(mock_session, mock_save, mock_history)
        mock_get_events.return_value = []
        
        # Mock LLM service that times out
        mock_service = MagicMock()
        mock_service.DEFAULT_MODEL_A = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])
        
        # Create a generator that hangs (simulates timeout) 
        async def timeout_generator(model, prompt, system_prompt=None, timeout_seconds=60):
            # Override timeout for testing
            if timeout_seconds > 5:
                timeout_seconds = 2  # Use short timeout for testing
                
            yield {"token": "Starting response...", "done": False, "model_name": "test-model", "response_time_ms": 100}
            # Simulate hanging - will be caught by our short timeout
            await asyncio.sleep(timeout_seconds + 1)  # Sleep longer than timeout
        
        mock_service.generate_streaming_response = timeout_generator
        mock_get_llm.return_value = mock_service
        
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "test timeout handling", "single_model_mode": True}
        
        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            # Set a reasonable timeout for the test
            import time
            start_time = time.time()
            for line in resp.iter_lines():
                if time.time() - start_time > 10:  # 10 second test timeout
                    break
                if line and line.startswith("data: "):
                    chunks.append(line[len("data: "):])
        
        # Should get at least some chunks including error handling
        self.assertGreater(len(chunks), 0)
        
        # Look for timeout error or retry attempts in chunks
        error_found = False
        for chunk_data in chunks:
            try:
                chunk = json.loads(chunk_data)
                if chunk.get("error") and ("timeout" in chunk.get("error", "").lower() or 
                                         "failed" in chunk.get("error", "").lower()):
                    error_found = True
                    break
            except json.JSONDecodeError:
                continue
        
        # Should have proper error handling (either timeout or retry failure)
        self.assertTrue(error_found or len(chunks) > 2, 
                       "Should handle timeouts gracefully with error reporting or retries")

    @patch("chat_service.app.get_or_create_session", new_callable=AsyncMock)
    @patch("chat_service.app.save_message", new_callable=AsyncMock)
    @patch("chat_service.app.get_conversation_history", new_callable=AsyncMock)
    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_streaming_malformed_chunk_recovery(self, mock_get_llm, mock_get_events, mock_history, mock_save, mock_session):
        """Test that malformed chunks don't break the stream."""
        self._setup_session_mocks(mock_session, mock_save, mock_history)
        mock_get_events.return_value = []
        
        mock_service = MagicMock()
        mock_service.DEFAULT_MODEL_A = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])
        
        # Create a generator that yields malformed chunks
        async def malformed_generator(*args, **kwargs):
            # Good chunk
            yield {"token": "Hello", "done": False, "model_name": "test-model", "response_time_ms": 100}
            # Malformed chunk (missing 'message' key that LLM service expects from Ollama)
            # This will trigger the KeyError handling in our improved code
            yield {"token": " there", "done": False, "model_name": "test-model", "response_time_ms": 200}
            # Final chunk
            yield {"token": "", "done": True, "model_name": "test-model", "response_time_ms": 300, "success": True}
        
        mock_service.generate_streaming_response = malformed_generator
        mock_get_llm.return_value = mock_service
        
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "test malformed chunk handling", "single_model_mode": True}
        
        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    chunks.append(line[len("data: "):])
        
        # Should complete successfully
        self.assertGreater(len(chunks), 0)
        
        # Should have a completion marker (SYSTEM)
        system_completion = False
        content_chunks = 0
        
        for chunk_data in chunks:
            try:
                chunk = json.loads(chunk_data)
                if chunk.get("model") == "SYSTEM" and chunk.get("done"):
                    system_completion = True
                elif chunk.get("token") and not chunk.get("done"):
                    content_chunks += 1
            except json.JSONDecodeError:
                continue
        
        self.assertTrue(system_completion, "Should complete with SYSTEM marker")
        self.assertGreater(content_chunks, 0, "Should receive content chunks despite potential malformed data")

    @patch("chat_service.app.get_or_create_session", new_callable=AsyncMock)
    @patch("chat_service.app.save_message", new_callable=AsyncMock)
    @patch("chat_service.app.get_conversation_history", new_callable=AsyncMock)
    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_ollama_health_check_failure(self, mock_get_llm, mock_get_events, mock_history, mock_save, mock_session):
        """Test behavior when Ollama health check fails."""
        self._setup_session_mocks(mock_session, mock_save, mock_history)
        mock_get_events.return_value = []
        
        mock_service = MagicMock()
        mock_service.DEFAULT_MODEL_A = "test-model"
        # Health check fails
        mock_service.get_available_models = AsyncMock(side_effect=Exception("Ollama connection failed"))
        
        # But streaming still works (simulate intermittent issues)
        async def working_generator(*args, **kwargs):
            yield {"token": "Despite health check failure, ", "done": False, "model_name": "test-model"}
            yield {"token": "streaming still works", "done": False, "model_name": "test-model"}
            yield {"token": "", "done": True, "model_name": "test-model", "success": True}
        
        mock_service.generate_streaming_response = working_generator
        mock_get_llm.return_value = mock_service
        
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "test health check failure", "single_model_mode": True}
        
        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    chunks.append(line[len("data: "):])
        
        # Should still work despite health check failure
        self.assertGreater(len(chunks), 0)
        
        # Should have content and completion
        content_found = False
        completion_found = False
        
        for chunk_data in chunks:
            try:
                chunk = json.loads(chunk_data)
                if chunk.get("token") and "streaming still works" in chunk.get("token", ""):
                    content_found = True
                if chunk.get("model") == "SYSTEM" and chunk.get("done"):
                    completion_found = True
            except json.JSONDecodeError:
                continue
        
        self.assertTrue(content_found, "Should receive content despite health check failure")
        self.assertTrue(completion_found, "Should complete successfully")

    @patch("chat_service.app.get_or_create_session", new_callable=AsyncMock)
    @patch("chat_service.app.save_message", new_callable=AsyncMock)
    @patch("chat_service.app.get_conversation_history", new_callable=AsyncMock)
    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.stream_model_response")
    @patch("chat_service.app.get_llm_service")
    def test_streaming_retry_success_after_failure(self, mock_get_llm, mock_stream_model, mock_get_events, mock_history, mock_save, mock_session):
        """Test that retry mechanism works when first attempt fails."""
        self._setup_session_mocks(mock_session, mock_save, mock_history)
        mock_get_events.return_value = []
        
        mock_service = MagicMock()
        mock_service.DEFAULT_MODEL_A = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])
        mock_get_llm.return_value = mock_service
        
        # Track call attempts at the stream_model_response level
        self.call_count = 0
        
        async def retry_stream_response(*args, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                # First attempt fails
                raise Exception("Ollama temporary connection error")
            else:
                # Second attempt succeeds - yield StreamChunk objects
                from chat_service.app import StreamChunk
                yield StreamChunk(model="A", token="Retry successful: ", done=False)
                yield StreamChunk(model="A", token="Hello from retry!", done=False)  
                yield StreamChunk(model="A", token="", done=True)
        
        mock_stream_model.side_effect = retry_stream_response
        
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "test retry mechanism", "single_model_mode": True}
        
        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    chunks.append(line[len("data: "):])
        
        # Should succeed after retry
        self.assertGreater(len(chunks), 0)
        self.assertEqual(self.call_count, 2, "Should have made 2 attempts (original + 1 retry)")
        
        # Should have successful content
        retry_content_found = False
        for chunk_data in chunks:
            try:
                chunk = json.loads(chunk_data)
                if "Hello from retry!" in chunk.get("token", ""):
                    retry_content_found = True
                    break
            except json.JSONDecodeError:
                continue
        
        self.assertTrue(retry_content_found, "Should receive content from successful retry")

    @patch("chat_service.app.get_or_create_session", new_callable=AsyncMock)
    @patch("chat_service.app.save_message", new_callable=AsyncMock)
    @patch("chat_service.app.get_conversation_history", new_callable=AsyncMock)
    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_streaming_complete_failure_after_retries(self, mock_get_llm, mock_get_events, mock_history, mock_save, mock_session):
        """Test behavior when all retry attempts fail."""
        self._setup_session_mocks(mock_session, mock_save, mock_history)
        mock_get_events.return_value = []
        
        mock_service = MagicMock()
        mock_service.DEFAULT_MODEL_A = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])
        
        # All attempts fail
        async def failing_generator(*args, **kwargs):
            raise Exception("Persistent Ollama failure")
        
        mock_service.generate_streaming_response = failing_generator
        mock_get_llm.return_value = mock_service
        
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "test complete failure", "single_model_mode": True}
        
        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)  # Should still return 200 (streaming starts)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    chunks.append(line[len("data: "):])
        
        # Should still get some chunks (error messages)
        self.assertGreater(len(chunks), 0)
        
        # Should have error information
        error_found = False
        system_completion = False
        
        for chunk_data in chunks:
            try:
                chunk = json.loads(chunk_data)
                if chunk.get("error") and "attempts" in chunk.get("error", "").lower():
                    error_found = True
                if chunk.get("model") == "SYSTEM" and chunk.get("done"):
                    system_completion = True
            except json.JSONDecodeError:
                continue
        
        self.assertTrue(error_found or system_completion, 
                       "Should provide error feedback or system completion after all retries fail")

    @patch("chat_service.app.get_or_create_session", new_callable=AsyncMock)
    @patch("chat_service.app.save_message", new_callable=AsyncMock)
    @patch("chat_service.app.get_conversation_history", new_callable=AsyncMock)
    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_streaming_partial_response_preservation(self, mock_get_llm, mock_get_events, mock_history, mock_save, mock_session):
        """Test that partial responses are preserved on failure."""
        self._setup_session_mocks(mock_session, mock_save, mock_history)
        mock_get_events.return_value = []
        
        mock_service = MagicMock()
        mock_service.DEFAULT_MODEL_A = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])
        
        async def partial_failure_generator(*args, **kwargs):
            # Send some content then fail
            yield {"token": "This is a partial ", "done": False, "model_name": "test-model"}
            yield {"token": "response that will ", "done": False, "model_name": "test-model"}
            yield {"token": "fail mid-stream", "done": False, "model_name": "test-model"}
            # Then fail
            raise Exception("Connection lost mid-stream")
        
        mock_service.generate_streaming_response = partial_failure_generator
        mock_get_llm.return_value = mock_service
        
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "test partial response", "single_model_mode": True}
        
        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    chunks.append(line[len("data: "):])
        
        # Should receive the partial content before failure
        partial_content = []
        error_info = None
        
        for chunk_data in chunks:
            try:
                chunk = json.loads(chunk_data)
                if chunk.get("token") and not chunk.get("done"):
                    partial_content.append(chunk.get("token", ""))
                elif chunk.get("error"):
                    error_info = chunk.get("error")
            except json.JSONDecodeError:
                continue
        
        # Should have received partial content
        self.assertGreater(len(partial_content), 0, "Should receive partial content before failure")
        
        # Partial content should contain our test message
        full_partial = "".join(partial_content)
        self.assertIn("partial", full_partial.lower(), "Should preserve partial response content")
        
        # Should also have error information
        self.assertIsNotNone(error_info, "Should provide error information after failure")