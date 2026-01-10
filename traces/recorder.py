"""
TraceRecorder - Shared service for recording trace events.

Can be used from both Django admin and FastAPI to instrument the chat pipeline.

Usage:
    recorder = TraceRecorder(run_id=uuid, persist=True)

    recorder.event('input', {'message': '...', 'filters': {...}})

    with recorder.span('retrieval') as span:
        results = rag_service.search(...)
        span.data = {'candidates': [...], 'count': len(results)}

    recorder.finalize(status='success', final_answer='...')
"""

import time
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class SpanData:
    """Mutable container for span data that can be set during execution."""
    data: dict = field(default_factory=dict)

    def update(self, **kwargs):
        """Update span data with keyword arguments."""
        self.data.update(kwargs)


class TraceRecorder:
    """
    Records trace events for a debug run.

    Events are stored in memory and optionally persisted to the database
    immediately for real-time visibility during streaming.
    """

    def __init__(self, run_id: UUID, persist: bool = True):
        """
        Initialize a trace recorder.

        Args:
            run_id: The ChatDebugRun UUID this recorder is for
            persist: If True, persist events to DB immediately. If False, only keep in memory.
        """
        self.run_id = run_id
        self.persist = persist
        self.events: list[dict] = []
        self._seq = 0
        self._start_time = time.time()
        self._run_updated = False

    def event(
        self,
        stage: str,
        data: dict,
        latency_ms: int = None,
        error_type: str = None,
        error_severity: str = None,
    ) -> dict:
        """
        Record a trace event.

        Args:
            stage: Event stage (input, retrieval, context_block, etc.)
            data: Event payload data
            latency_ms: Optional latency measurement in milliseconds
            error_type: For error events: 'rag_error', 'llm_error', 'location_error', etc.
            error_severity: For error events: 'critical', 'error', 'warning', 'info'

        Returns:
            The recorded event dict
        """
        self._seq += 1
        event = {
            'seq': self._seq,
            'stage': stage,
            'data': data,
            'latency_ms': latency_ms,
            'timestamp': time.time(),
            'error_type': error_type,
            'error_severity': error_severity,
        }
        self.events.append(event)

        if self.persist:
            self._persist_event(event)

        logger.debug(f'Trace event [{self.run_id}] #{self._seq}: {stage}')
        return event

    @contextmanager
    def span(self, stage: str):
        """
        Context manager that auto-captures latency and records an event on exit.

        Usage:
            with recorder.span('retrieval') as span:
                results = do_something()
                span.data = {'count': len(results), 'results': results}

        Args:
            stage: Event stage name

        Yields:
            SpanData object - set span.data dict to include in the event
        """
        span_data = SpanData()
        start = time.time()
        try:
            yield span_data
        finally:
            latency_ms = int((time.time() - start) * 1000)
            self.event(stage, span_data.data, latency_ms=latency_ms)

    def finalize(
        self,
        status: str,
        final_answer: str = '',
        error_message: str = '',
        error_stack: str = '',
        diagnostics: dict = None,
    ):
        """
        Complete the run and update the ChatDebugRun record.

        Args:
            status: Final status ('success' or 'error')
            final_answer: The LLM response text
            error_message: Error message if status is 'error'
            error_stack: Error stack trace if status is 'error'
            diagnostics: Computed diagnostics dict
        """
        total_ms = int((time.time() - self._start_time) * 1000)

        if self.persist:
            self._update_run(
                status=status,
                final_answer=final_answer,
                error_message=error_message,
                error_stack=error_stack,
                total_latency_ms=total_ms,
                diagnostics=diagnostics,
            )
            self._run_updated = True

        logger.info(f'Trace finalized [{self.run_id}]: {status} in {total_ms}ms')

    def mark_running(self):
        """Mark the run as running (called when execution starts)."""
        if self.persist:
            from traces.models import ChatDebugRun
            ChatDebugRun.objects.filter(id=self.run_id).update(status='running')

    def _persist_event(self, event: dict):
        """Persist a single event to the database."""
        from traces.models import ChatDebugEvent

        try:
            ChatDebugEvent.objects.create(
                run_id=self.run_id,
                seq=event['seq'],
                stage=event['stage'],
                data=event['data'],
                latency_ms=event['latency_ms'],
                error_type=event.get('error_type'),
                error_severity=event.get('error_severity'),
            )
        except Exception as e:
            logger.error(f'Failed to persist trace event: {e}')

    def _update_run(
        self,
        status: str,
        final_answer: str,
        error_message: str,
        error_stack: str,
        total_latency_ms: int,
        diagnostics: dict = None,
    ):
        """Update the ChatDebugRun record with final results."""
        from traces.models import ChatDebugRun

        try:
            ChatDebugRun.objects.filter(id=self.run_id).update(
                status=status,
                final_answer_text=final_answer,
                error_message=error_message,
                error_stack=error_stack,
                total_latency_ms=total_latency_ms,
                diagnostics=diagnostics,
            )
        except Exception as e:
            logger.error(f'Failed to update trace run: {e}')

    def get_events_by_stage(self, stage: str) -> list[dict]:
        """Get all events of a particular stage."""
        return [e for e in self.events if e['stage'] == stage]

    def get_total_latency_ms(self) -> int:
        """Get total elapsed time since recorder was created."""
        return int((time.time() - self._start_time) * 1000)

    async def event_async(
        self,
        stage: str,
        data: dict,
        latency_ms: int = None,
        error_type: str = None,
        error_severity: str = None,
    ) -> dict:
        """
        Record a trace event from async context.

        This wraps the synchronous event() method with sync_to_async
        to ensure proper database transaction handling in async code.

        Args:
            stage: Event stage (input, retrieval, context_block, etc.)
            data: Event payload data
            latency_ms: Optional latency measurement in milliseconds
            error_type: For error events: 'rag_error', 'llm_error', 'location_error', etc.
            error_severity: For error events: 'critical', 'error', 'warning', 'info'

        Returns:
            The recorded event dict
        """
        from asgiref.sync import sync_to_async

        @sync_to_async
        def _record_event():
            return self.event(stage, data, latency_ms, error_type, error_severity)

        return await _record_event()

    async def finalize_async(
        self,
        status: str,
        final_answer: str = '',
        error_message: str = '',
        error_stack: str = '',
        diagnostics: dict = None,
    ):
        """
        Complete the run from async context.

        This wraps the synchronous finalize() method with sync_to_async
        to ensure proper database transaction handling in async code.
        """
        from asgiref.sync import sync_to_async

        @sync_to_async
        def _finalize():
            self.finalize(status, final_answer, error_message, error_stack, diagnostics)

        await _finalize()


class NullRecorder:
    """
    No-op recorder for when tracing is disabled.

    All methods are no-ops, so code can unconditionally call trace methods.
    """

    def event(self, stage: str, data: dict, latency_ms: int = None, error_type: str = None, error_severity: str = None) -> None:
        pass

    @contextmanager
    def span(self, stage: str):
        yield SpanData()

    def finalize(self, *args, **kwargs):
        pass

    def mark_running(self):
        pass
