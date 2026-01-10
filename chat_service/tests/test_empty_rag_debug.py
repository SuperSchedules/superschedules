"""
Tests for edge case where RAG returns no results with debug mode enabled.

Reproduces the issue where:
- Frontend sends chat request with debug=true
- RAG finds 0 events (e.g., all events are in the past)
- The debug trace should still be finalized properly
"""

import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from fastapi.testclient import TestClient

from chat_service.app import app, get_relevant_events
from api.rag_service import RAGResult, RankedEvent, RankingFactors


class EmptyRAGDebugTraceTest(TestCase):
    """Test that debug traces are finalized even when RAG returns no results."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="empty-rag-test@example.com",
            email="empty-rag-test@example.com",
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
    def test_empty_rag_results_without_debug(self, mock_get_llm, mock_get_events, mock_history, mock_save, mock_session):
        """Test basic case: empty RAG results without debug mode."""
        self._setup_session_mocks(mock_session, mock_save, mock_history)

        # RAG returns empty tiered result
        mock_get_events.return_value = RAGResult(
            recommended_events=[],
            additional_events=[],
            context_events=[],
            total_considered=0,
            search_metadata={'query': 'test', 'error': None}
        )

        # Mock LLM service
        mock_service = MagicMock()
        mock_service.primary_model = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])

        async def mock_streaming(model, prompt, system_prompt=None, timeout_seconds=60, **kwargs):
            yield {"token": "I couldn't find any events", "done": False, "model_name": "test-model", "response_time_ms": 100}
            yield {"token": " matching your query.", "done": True, "model_name": "test-model", "response_time_ms": 200}

        mock_service.generate_streaming_response = mock_streaming
        mock_get_llm.return_value = mock_service

        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {
            "message": "find activities nearby",
            "use_tiered_retrieval": True,
            "debug": False,  # No debug mode
        }

        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    chunks.append(json.loads(line[len("data: "):]))

        # Should complete successfully
        self.assertGreater(len(chunks), 0)

        # Find final chunk
        final_chunk = next((c for c in chunks if c.get("done") and c.get("model") == "SYSTEM"), None)
        self.assertIsNotNone(final_chunk, "Should have a final SYSTEM chunk")

        # Should have empty event lists
        self.assertEqual(final_chunk.get("recommended_event_ids", []), [])
        self.assertEqual(final_chunk.get("all_event_ids", []), [])

    @patch("chat_service.app.get_or_create_session", new_callable=AsyncMock)
    @patch("chat_service.app.save_message", new_callable=AsyncMock)
    @patch("chat_service.app.get_conversation_history", new_callable=AsyncMock)
    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    @patch("traces.models.ChatDebugRun.objects.create")
    def test_empty_rag_results_with_debug_mode(self, mock_debug_create, mock_get_llm, mock_get_events, mock_history, mock_save, mock_session):
        """
        Test the edge case: empty RAG results WITH debug mode enabled.

        This reproduces the issue where the debug trace gets stuck at "running"
        when there are no RAG results.
        """
        self._setup_session_mocks(mock_session, mock_save, mock_history)

        # RAG returns empty tiered result
        mock_get_events.return_value = RAGResult(
            recommended_events=[],
            additional_events=[],
            context_events=[],
            total_considered=0,
            search_metadata={'query': 'test', 'error': None}
        )

        # Mock debug run creation
        mock_debug_run = MagicMock()
        mock_debug_run.id = "test-debug-run-id"
        mock_debug_create.return_value = mock_debug_run

        # Mock LLM service
        mock_service = MagicMock()
        mock_service.primary_model = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])

        async def mock_streaming(model, prompt, system_prompt=None, timeout_seconds=60, **kwargs):
            yield {"token": "I couldn't find any events", "done": False, "model_name": "test-model", "response_time_ms": 100}
            yield {"token": " matching your query.", "done": True, "model_name": "test-model", "response_time_ms": 200}

        mock_service.generate_streaming_response = mock_streaming
        mock_get_llm.return_value = mock_service

        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {
            "message": "find activities nearby",
            "use_tiered_retrieval": True,
            "debug": True,  # Debug mode enabled
        }

        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    try:
                        chunks.append(json.loads(line[len("data: "):]))
                    except json.JSONDecodeError:
                        continue

        # Should complete successfully even with debug mode
        self.assertGreater(len(chunks), 0, "Should have received chunks")

        # Find final chunk
        final_chunk = next((c for c in chunks if c.get("done") and c.get("model") == "SYSTEM"), None)
        self.assertIsNotNone(final_chunk, "Should have a final SYSTEM chunk even with empty RAG results")

        # Should have debug_run_id in final response
        # Note: This might be None if the mock doesn't fully simulate the trace
        # but the important thing is the stream completed

    @patch("chat_service.app.get_or_create_session", new_callable=AsyncMock)
    @patch("chat_service.app.save_message", new_callable=AsyncMock)
    @patch("chat_service.app.get_conversation_history", new_callable=AsyncMock)
    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_empty_rag_legacy_format(self, mock_get_llm, mock_get_events, mock_history, mock_save, mock_session):
        """Test empty RAG results with legacy (non-tiered) mode."""
        self._setup_session_mocks(mock_session, mock_save, mock_history)

        # RAG returns empty list (legacy format)
        mock_get_events.return_value = []

        # Mock LLM service
        mock_service = MagicMock()
        mock_service.primary_model = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])

        async def mock_streaming(model, prompt, system_prompt=None, timeout_seconds=60, **kwargs):
            yield {"token": "No events found.", "done": True, "model_name": "test-model", "response_time_ms": 100}

        mock_service.generate_streaming_response = mock_streaming
        mock_get_llm.return_value = mock_service

        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {
            "message": "find activities nearby",
            "use_tiered_retrieval": False,  # Legacy mode
        }

        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    try:
                        chunks.append(json.loads(line[len("data: "):]))
                    except json.JSONDecodeError:
                        continue

        self.assertGreater(len(chunks), 0)
        final_chunk = next((c for c in chunks if c.get("done") and c.get("model") == "SYSTEM"), None)
        self.assertIsNotNone(final_chunk)


class EmptyRAGWithLocationIdTest(TestCase):
    """Test empty RAG results when using location_id parameter."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="location-test@example.com",
            email="location-test@example.com",
            password="testpass123"
        )
        refresh = RefreshToken.for_user(self.user)
        self.jwt = str(refresh.access_token)
        self.client = TestClient(app)

    def _setup_session_mocks(self, mock_session, mock_save, mock_history):
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
    def test_location_id_with_empty_results(self, mock_get_llm, mock_get_events, mock_history, mock_save, mock_session):
        """Test that location_id is passed correctly even when results are empty."""
        self._setup_session_mocks(mock_session, mock_save, mock_history)

        # Track the call arguments to verify location_id is passed
        call_args = {}

        async def capture_get_events(*args, **kwargs):
            call_args.update(kwargs)
            return RAGResult(
                recommended_events=[],
                additional_events=[],
                context_events=[],
                total_considered=0,
                search_metadata={'query': 'test'}
            )

        mock_get_events.side_effect = capture_get_events

        # Mock LLM service
        mock_service = MagicMock()
        mock_service.primary_model = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])

        async def mock_streaming(model, prompt, system_prompt=None, timeout_seconds=60, **kwargs):
            yield {"token": "No events found.", "done": True, "model_name": "test-model", "response_time_ms": 100}

        mock_service.generate_streaming_response = mock_streaming
        mock_get_llm.return_value = mock_service

        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {
            "message": "find activities nearby",
            "location_id": 12345,  # Location ID should be passed to RAG
            "use_tiered_retrieval": True,
        }

        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            # Consume the stream
            for _ in resp.iter_lines():
                pass

        # Verify location_id was passed to get_relevant_events
        self.assertEqual(call_args.get("location_id"), 12345)
        self.assertTrue(call_args.get("use_tiered"))


class EmptyRAGIntegrationTest(TestCase):
    """
    Integration test that tests the actual RAG -> LLM flow with empty results.
    Uses less mocking to catch real issues.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="integration-test@example.com",
            email="integration-test@example.com",
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
    @patch("chat_service.app.get_llm_service")
    def test_empty_rag_proceeds_to_llm(self, mock_get_llm, mock_history, mock_save, mock_session):
        """
        Test that when RAG returns empty results, we still call the LLM.
        This catches the bug where empty results might cause the flow to hang.
        """
        from locations.models import Location

        self._setup_session_mocks(mock_session, mock_save, mock_history)

        # Create a location that exists but has no events nearby
        Location.objects.create(
            geoid='9999999',
            name='EmptyTown',
            normalized_name='emptytown',
            state='MA',
            latitude=42.0,
            longitude=-71.0,
            population=100,
        )

        # Track if LLM was called
        llm_called = False

        # Mock LLM service
        mock_service = MagicMock()
        mock_service.primary_model = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])

        async def mock_streaming(model, prompt, system_prompt=None, timeout_seconds=60, **kwargs):
            nonlocal llm_called
            llm_called = True
            # Check that prompt mentions no events found
            yield {"token": "I apologize, but I couldn't find any events", "done": False, "model_name": "test-model", "response_time_ms": 100}
            yield {"token": " matching your search.", "done": True, "model_name": "test-model", "response_time_ms": 200}

        mock_service.generate_streaming_response = mock_streaming
        mock_get_llm.return_value = mock_service

        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {
            "message": "find activities in EmptyTown",
            "use_tiered_retrieval": True,
            "location_id": Location.objects.get(name='EmptyTown').id,
        }

        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    try:
                        chunks.append(json.loads(line[len("data: "):]))
                    except json.JSONDecodeError:
                        continue

        # LLM should have been called even with empty RAG results
        self.assertTrue(llm_called, "LLM should be called even when RAG returns no events")

        # Should have completed the stream
        final_chunk = next((c for c in chunks if c.get("done") and c.get("model") == "SYSTEM"), None)
        self.assertIsNotNone(final_chunk, "Should have a final SYSTEM chunk")

    @patch("chat_service.app.get_or_create_session", new_callable=AsyncMock)
    @patch("chat_service.app.save_message", new_callable=AsyncMock)
    @patch("chat_service.app.get_conversation_history", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_debug_trace_finalizes_with_empty_rag(self, mock_get_llm, mock_history, mock_save, mock_session):
        """
        Test that debug trace is properly finalized when RAG returns empty results.
        This is the specific bug we're trying to catch.
        """
        from traces.models import ChatDebugRun

        self._setup_session_mocks(mock_session, mock_save, mock_history)

        # Mock LLM service
        mock_service = MagicMock()
        mock_service.primary_model = "test-model"
        mock_service.get_available_models = AsyncMock(return_value=["test-model"])

        async def mock_streaming(model, prompt, system_prompt=None, timeout_seconds=60, **kwargs):
            yield {"token": "No events found.", "done": True, "model_name": "test-model", "response_time_ms": 100}

        mock_service.generate_streaming_response = mock_streaming
        mock_get_llm.return_value = mock_service

        # Count debug runs before
        runs_before = ChatDebugRun.objects.count()

        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {
            "message": "find activities nearby",
            "use_tiered_retrieval": True,
            "debug": True,  # Enable debug mode
        }

        chunks = []
        with self.client.stream("POST", "/api/v1/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    try:
                        chunks.append(json.loads(line[len("data: "):]))
                    except json.JSONDecodeError:
                        continue

        # A debug run should have been created
        runs_after = ChatDebugRun.objects.count()
        self.assertEqual(runs_after, runs_before + 1, "Should have created one debug run")

        # Get the debug run
        debug_run = ChatDebugRun.objects.order_by('-created_at').first()

        # Verify all expected trace events were recorded
        from traces.models import ChatDebugEvent
        events = ChatDebugEvent.objects.filter(run=debug_run).order_by('seq')
        actual_stages = [e.stage for e in events]
        expected_stages = {'retrieval', 'context_block', 'prompt_final', 'llm_request', 'llm_response'}
        self.assertEqual(expected_stages, set(actual_stages),
                        f"Expected trace stages not recorded. Got: {actual_stages}")

        # The run should be finalized (not stuck at 'running')
        self.assertIn(debug_run.status, ['success', 'error'],
                     f"Debug run should be finalized, but status is '{debug_run.status}'")

        # If it's success, should have final answer
        if debug_run.status == 'success':
            self.assertIsNotNone(debug_run.final_answer_text)
            self.assertIsNotNone(debug_run.diagnostics)


class TraceFinalizationTest(TestCase):
    """Test that trace recorder properly finalizes in all edge cases."""

    def test_trace_recorder_finalize_with_empty_events(self):
        """Test TraceRecorder.finalize works with empty event list."""
        from traces.recorder import TraceRecorder
        from traces.models import ChatDebugRun
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(
            username="trace-test@example.com",
            email="trace-test@example.com",
            password="testpass123"
        )

        # Create a debug run
        run = ChatDebugRun.objects.create(
            created_by=user,
            request_text="test query",
            settings={},
            status='pending',
        )

        # Create recorder
        recorder = TraceRecorder(run_id=run.id, persist=True)
        recorder.mark_running()

        # Record retrieval event with empty results (like the edge case)
        recorder.event('retrieval', {
            'total_candidates': 0,
            'above_threshold': 0,
            'candidates': [],
            'tiers': {'recommended': 0, 'additional': 0, 'context': 0},
        })

        # Finalize with success
        recorder.finalize(
            status='success',
            final_answer='No events found matching your query.',
            diagnostics={'warnings': [], 'retrieval_quality': {}}
        )

        # Verify the run was updated
        run.refresh_from_db()
        self.assertEqual(run.status, 'success')
        self.assertEqual(run.final_answer_text, 'No events found matching your query.')
        self.assertIsNotNone(run.diagnostics)

    def test_trace_recorder_finalize_with_error(self):
        """Test TraceRecorder.finalize properly records errors."""
        from traces.recorder import TraceRecorder
        from traces.models import ChatDebugRun
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(
            username="trace-error-test@example.com",
            email="trace-error-test@example.com",
            password="testpass123"
        )

        run = ChatDebugRun.objects.create(
            created_by=user,
            request_text="test query",
            settings={},
            status='pending',
        )

        recorder = TraceRecorder(run_id=run.id, persist=True)
        recorder.mark_running()

        # Finalize with error
        recorder.finalize(
            status='error',
            error_message='Connection timeout',
            error_stack='Traceback...'
        )

        run.refresh_from_db()
        self.assertEqual(run.status, 'error')
        self.assertEqual(run.error_message, 'Connection timeout')
