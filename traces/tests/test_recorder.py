"""
Tests for TraceRecorder service.
"""

import uuid
from django.test import TestCase
from django.contrib.auth.models import User

from traces.models import ChatDebugRun, ChatDebugEvent
from traces.recorder import TraceRecorder, SpanData, NullRecorder


class TestSpanData(TestCase):
    """Tests for SpanData helper class."""

    def test_span_data_defaults_to_empty_dict(self):
        span = SpanData()
        self.assertEqual(span.data, {})

    def test_span_data_update(self):
        span = SpanData()
        span.update(count=5, items=['a', 'b'])
        self.assertEqual(span.data, {'count': 5, 'items': ['a', 'b']})


class TestTraceRecorder(TestCase):
    """Tests for TraceRecorder service."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.run = ChatDebugRun.objects.create(
            created_by=self.user,
            request_text='test query',
            settings={'max_events': 10},
        )

    def test_event_increments_sequence(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=False)

        e1 = recorder.event('input', {'message': 'hello'})
        e2 = recorder.event('retrieval', {'count': 5})
        e3 = recorder.event('llm_response', {'text': 'world'})

        self.assertEqual(e1['seq'], 1)
        self.assertEqual(e2['seq'], 2)
        self.assertEqual(e3['seq'], 3)

    def test_event_stores_in_memory(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=False)

        recorder.event('input', {'message': 'hello'})
        recorder.event('retrieval', {'count': 5})

        self.assertEqual(len(recorder.events), 2)
        self.assertEqual(recorder.events[0]['stage'], 'input')
        self.assertEqual(recorder.events[1]['stage'], 'retrieval')

    def test_event_persists_to_database(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=True)

        recorder.event('input', {'message': 'hello'})
        recorder.event('retrieval', {'count': 5})

        events = ChatDebugEvent.objects.filter(run=self.run).order_by('seq')
        self.assertEqual(events.count(), 2)
        self.assertEqual(events[0].stage, 'input')
        self.assertEqual(events[0].data, {'message': 'hello'})
        self.assertEqual(events[1].stage, 'retrieval')
        self.assertEqual(events[1].data, {'count': 5})

    def test_event_with_latency(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=True)

        recorder.event('retrieval', {'count': 5}, latency_ms=150)

        event = ChatDebugEvent.objects.get(run=self.run)
        self.assertEqual(event.latency_ms, 150)

    def test_span_context_manager(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=False)

        with recorder.span('retrieval') as span:
            span.data = {'count': 10, 'query': 'test'}

        self.assertEqual(len(recorder.events), 1)
        self.assertEqual(recorder.events[0]['stage'], 'retrieval')
        self.assertEqual(recorder.events[0]['data']['count'], 10)
        self.assertIsNotNone(recorder.events[0]['latency_ms'])

    def test_span_measures_latency(self):
        import time
        recorder = TraceRecorder(run_id=self.run.id, persist=False)

        with recorder.span('slow_operation') as span:
            time.sleep(0.05)  # 50ms
            span.data = {'done': True}

        latency = recorder.events[0]['latency_ms']
        self.assertGreaterEqual(latency, 45)  # Allow some variance

    def test_finalize_updates_run(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=True)

        recorder.event('input', {'message': 'hello'})
        recorder.finalize(
            status='success',
            final_answer='The answer is 42',
            diagnostics={'warnings': []},
        )

        self.run.refresh_from_db()
        self.assertEqual(self.run.status, 'success')
        self.assertEqual(self.run.final_answer_text, 'The answer is 42')
        self.assertEqual(self.run.diagnostics, {'warnings': []})
        self.assertIsNotNone(self.run.total_latency_ms)

    def test_finalize_with_error(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=True)

        recorder.finalize(
            status='error',
            error_message='Something went wrong',
            error_stack='Traceback...',
        )

        self.run.refresh_from_db()
        self.assertEqual(self.run.status, 'error')
        self.assertEqual(self.run.error_message, 'Something went wrong')
        self.assertEqual(self.run.error_stack, 'Traceback...')

    def test_mark_running(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=True)

        self.assertEqual(self.run.status, 'pending')
        recorder.mark_running()

        self.run.refresh_from_db()
        self.assertEqual(self.run.status, 'running')

    def test_get_events_by_stage(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=False)

        recorder.event('context_block', {'type': 'system'})
        recorder.event('retrieval', {'count': 5})
        recorder.event('context_block', {'type': 'events'})
        recorder.event('context_block', {'type': 'history'})

        blocks = recorder.get_events_by_stage('context_block')
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0]['data']['type'], 'system')
        self.assertEqual(blocks[1]['data']['type'], 'events')
        self.assertEqual(blocks[2]['data']['type'], 'history')

    def test_non_persist_mode(self):
        recorder = TraceRecorder(run_id=self.run.id, persist=False)

        recorder.event('input', {'message': 'hello'})
        recorder.event('retrieval', {'count': 5})

        # Events should be in memory
        self.assertEqual(len(recorder.events), 2)

        # But not in database
        self.assertEqual(ChatDebugEvent.objects.filter(run=self.run).count(), 0)


class TestNullRecorder(TestCase):
    """Tests for NullRecorder (no-op recorder)."""

    def test_event_is_noop(self):
        recorder = NullRecorder()
        result = recorder.event('input', {'message': 'hello'})
        self.assertIsNone(result)

    def test_span_is_noop(self):
        recorder = NullRecorder()
        with recorder.span('test') as span:
            span.data = {'count': 5}
        # No exception raised, that's the test

    def test_finalize_is_noop(self):
        recorder = NullRecorder()
        recorder.finalize(status='success')
        # No exception raised, that's the test
