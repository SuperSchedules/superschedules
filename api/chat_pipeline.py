"""
Unified chat pipeline entrypoint.

Both FastAPI chat service and Django admin debug runner call this function
to ensure identical behavior and consistent tracing.
"""

import time
import logging
import traceback
from typing import Dict, Any, List, Optional, AsyncGenerator, TYPE_CHECKING
from datetime import datetime

from django.utils import timezone

from .rag_service import get_rag_service
from .llm_service import get_llm_service, create_event_discovery_prompt

if TYPE_CHECKING:
    from traces.recorder import TraceRecorder

logger = logging.getLogger(__name__)


async def run_chat_pipeline(
    message: str,
    settings: Dict[str, Any],
    trace: Optional['TraceRecorder'] = None,
    conversation_history: List[Dict[str, str]] = None,
    user_preferences: Dict[str, Any] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Unified chat pipeline entrypoint.

    Yields streaming chunks with optional trace events.

    Args:
        message: User's input message
        settings: Pipeline settings dict containing:
            - model: LLM model name (optional, uses default)
            - temperature: LLM temperature (optional, default 0.7)
            - max_events: Maximum events to retrieve (default 20)
            - similarity_threshold: Minimum similarity score (default 0.2)
            - time_filter_days: Days to look ahead (default 14)
            - location: Location string for filtering
            - max_distance_miles: Radius for geo filtering
            - is_virtual: Filter by virtual/in-person
            - date_from: Start of date range (ISO string)
            - date_to: End of date range (ISO string)
        trace: Optional TraceRecorder for debugging
        conversation_history: Previous messages in session
        user_preferences: User profile data

    Yields:
        {'type': 'token', 'content': str} - LLM token chunks
        {'type': 'done', 'full_response': str, 'event_ids': list} - Completion
        {'type': 'error', 'message': str, 'stack': str} - Error
    """
    from asgiref.sync import sync_to_async

    start_time = time.time()

    if trace:
        trace.mark_running()

    try:
        # 1. RAG retrieval (sync, run in thread pool)
        rag_service = get_rag_service()

        # Parse date strings if provided
        date_from = None
        date_to = None
        if settings.get('date_from'):
            try:
                date_from = datetime.fromisoformat(settings['date_from'].replace('Z', '+00:00'))
            except (ValueError, TypeError):
                pass
        if settings.get('date_to'):
            try:
                date_to = datetime.fromisoformat(settings['date_to'].replace('Z', '+00:00'))
            except (ValueError, TypeError):
                pass

        # Run RAG in thread pool (it uses Django ORM)
        context_events = await sync_to_async(rag_service.get_context_events)(
            user_message=message,
            max_events=settings.get('max_events', 20),
            similarity_threshold=settings.get('similarity_threshold', 0.2),
            time_filter_days=settings.get('time_filter_days', 14),
            date_from=date_from,
            date_to=date_to,
            location=settings.get('location'),
            is_virtual=settings.get('is_virtual'),
            max_distance_miles=settings.get('max_distance_miles'),
            user_lat=settings.get('user_lat'),
            user_lng=settings.get('user_lng'),
            default_state=settings.get('default_state', 'MA'),
            trace=trace,
        )

        # 2. Prompt assembly (sync, but fast)
        current_datetime = timezone.now()
        formatted_datetime = current_datetime.strftime("%A, %B %d, %Y at %I:%M %p")

        prompt_context = {
            'current_date': formatted_datetime,
            'location': settings.get('location'),
            'date_range': {
                'from': settings.get('date_from'),
                'to': settings.get('date_to'),
            } if settings.get('date_from') or settings.get('date_to') else None,
            'max_price': settings.get('max_price'),
        }

        system_prompt, user_prompt = create_event_discovery_prompt(
            message=message,
            events=context_events,
            context=prompt_context,
            conversation_history=conversation_history,
            user_preferences=user_preferences,
            trace=trace,
        )

        # 3. LLM streaming
        llm_service = get_llm_service()
        model_name = settings.get('model') or getattr(llm_service, '_primary_model', 'deepseek-llm:7b')

        if trace:
            trace.event('llm_request', {
                'model': model_name,
                'temperature': settings.get('temperature', 0.7),
                'prompt_chars': len(user_prompt),
                'system_chars': len(system_prompt),
            })

        full_response = ''
        llm_start = time.time()

        async for chunk in llm_service.generate_streaming_response(
            model=model_name,
            prompt=user_prompt,
            system_prompt=system_prompt,
            timeout_seconds=settings.get('timeout', 60),
        ):
            if chunk.get('token'):
                full_response += chunk['token']
                yield {'type': 'token', 'content': chunk['token']}

        llm_ms = int((time.time() - llm_start) * 1000)

        if trace:
            trace.event('llm_response', {
                'full_response': full_response,
                'chars': len(full_response),
                'tokens_est': len(full_response) // 4,
            }, latency_ms=llm_ms)

        # Extract event IDs for reference
        event_ids = [e['id'] for e in context_events[:5]] if context_events else []

        yield {
            'type': 'done',
            'full_response': full_response,
            'event_ids': event_ids,
            'total_latency_ms': int((time.time() - start_time) * 1000),
        }

    except Exception as e:
        error_stack = traceback.format_exc()
        logger.error(f'Chat pipeline error: {e}\n{error_stack}')

        if trace:
            trace.event('error', {
                'message': str(e),
                'stack': error_stack,
            })

        yield {
            'type': 'error',
            'message': str(e),
            'stack': error_stack,
        }


def run_chat_pipeline_sync(
    message: str,
    settings: Dict[str, Any],
    trace: Optional['TraceRecorder'] = None,
    conversation_history: List[Dict[str, str]] = None,
    user_preferences: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Synchronous version of the chat pipeline for Django admin.

    Returns the complete response instead of streaming.

    Returns:
        {
            'status': 'success' | 'error',
            'response': str,
            'event_ids': list,
            'total_latency_ms': int,
            'error_message': str,  # if error
            'error_stack': str,    # if error
        }
    """
    import asyncio

    async def collect_response():
        result = {
            'status': 'pending',
            'response': '',
            'event_ids': [],
            'total_latency_ms': 0,
        }

        async for chunk in run_chat_pipeline(
            message=message,
            settings=settings,
            trace=trace,
            conversation_history=conversation_history,
            user_preferences=user_preferences,
        ):
            if chunk['type'] == 'token':
                result['response'] += chunk['content']
            elif chunk['type'] == 'done':
                result['status'] = 'success'
                result['response'] = chunk['full_response']
                result['event_ids'] = chunk.get('event_ids', [])
                result['total_latency_ms'] = chunk.get('total_latency_ms', 0)
            elif chunk['type'] == 'error':
                result['status'] = 'error'
                result['error_message'] = chunk['message']
                result['error_stack'] = chunk['stack']

        return result

    # Run the async function in an event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(collect_response())
