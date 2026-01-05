"""
Views for chat debug tracing.

Provides:
- SSE streaming endpoint for debug runs
- API endpoint for creating debug runs
- JSON export endpoint
"""

import json
import time
import traceback
from uuid import UUID

from django.http import JsonResponse, StreamingHttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST, require_GET
from django.contrib.admin.views.decorators import staff_member_required

from .models import ChatDebugRun, ChatDebugEvent
from .recorder import TraceRecorder
from .diagnostics import compute_diagnostics, analyze_response_quality, compare_run_results


@staff_member_required
@require_POST
def create_debug_run(request):
    """
    Create a new debug run and return its ID.

    POST body (JSON):
    {
        "request_text": "activities for kids in Newton",
        "settings": {
            "model": "deepseek-llm:7b",
            "max_events": 10,
            "location": "Newton, MA",
            ...
        }
    }

    Returns:
    {
        "run_id": "uuid",
        "stream_url": "/admin/traces/chatdebugrun/<uuid>/stream/"
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON')

    request_text = data.get('request_text', '').strip()
    if not request_text:
        return HttpResponseBadRequest('request_text is required')

    settings = data.get('settings', {})

    # Create the run
    run = ChatDebugRun.objects.create(
        created_by=request.user,
        request_text=request_text,
        settings=settings,
        status='pending',
    )

    return JsonResponse({
        'run_id': str(run.id),
        'stream_url': f'/admin/traces/api/run/{run.id}/stream/',
        'detail_url': f'/admin/traces/chatdebugrun/{run.id}/change/',
    })


@staff_member_required
@require_GET
def stream_debug_run(request, run_id):
    """
    SSE endpoint that streams debug run progress.

    Sends events:
    - event: status  - Run status updates
    - event: trace   - Trace events as they're recorded
    - event: token   - LLM tokens as they stream
    - event: done    - Run complete with diagnostics
    - event: error   - Error occurred
    """
    try:
        run_uuid = UUID(str(run_id))
        run = ChatDebugRun.objects.get(id=run_uuid)
    except (ValueError, ChatDebugRun.DoesNotExist):
        return HttpResponseBadRequest('Invalid run_id')

    def event_stream():
        """Generator that yields SSE events."""
        # Send initial status
        yield f"event: status\ndata: {json.dumps({'status': 'running'})}\n\n"

        # Create trace recorder
        recorder = TraceRecorder(run_id=run.id, persist=True)
        recorder.mark_running()

        try:
            # Run the pipeline synchronously
            result = run_debug_pipeline(
                message=run.request_text,
                settings=run.settings,
                recorder=recorder,
            )

            if result['status'] == 'success':
                # Compute diagnostics
                diagnostics = compute_diagnostics(recorder.events)

                # Add response quality analysis
                retrieval_event = next((e for e in recorder.events if e['stage'] == 'retrieval'), None)
                if retrieval_event:
                    retrieved_events = retrieval_event['data'].get('candidates', [])
                    response_quality = analyze_response_quality(result['response'], retrieved_events)
                    diagnostics['response_quality'] = response_quality

                # Finalize the run
                recorder.finalize(
                    status='success',
                    final_answer=result['response'],
                    diagnostics=diagnostics,
                )

                # Send the response (not streaming individual tokens in sync mode)
                yield f"event: token\ndata: {json.dumps({'content': result['response']})}\n\n"

                # Send trace events
                for event in recorder.events:
                    event_data = {
                        'seq': event['seq'],
                        'stage': event['stage'],
                        'latency_ms': event.get('latency_ms'),
                    }
                    if event['stage'] == 'retrieval':
                        event_data['summary'] = {
                            'total_candidates': event['data'].get('total_candidates', 0),
                            'above_threshold': event['data'].get('above_threshold', 0),
                        }
                    elif event['stage'] == 'context_block':
                        event_data['summary'] = {
                            'block_type': event['data'].get('block_type'),
                            'chars': event['data'].get('chars', 0),
                            'tokens_est': event['data'].get('tokens_est', 0),
                        }
                    yield f"event: trace\ndata: {json.dumps(event_data)}\n\n"

                # Send done event
                yield f"event: done\ndata: {json.dumps({'diagnostics': diagnostics, 'run_id': str(run.id)})}\n\n"

            else:
                # Error case
                recorder.finalize(
                    status='error',
                    error_message=result.get('error_message', 'Unknown error'),
                    error_stack=result.get('error_stack', ''),
                )
                yield f"event: error\ndata: {json.dumps({'message': result.get('error_message', 'Unknown error')})}\n\n"

        except Exception as e:
            error_msg = str(e)
            error_stack = traceback.format_exc()

            recorder.finalize(
                status='error',
                error_message=error_msg,
                error_stack=error_stack,
            )

            yield f"event: error\ndata: {json.dumps({'message': error_msg})}\n\n"

    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


def run_debug_pipeline(message: str, settings: dict, recorder: TraceRecorder) -> dict:
    """
    Run the chat pipeline synchronously with tracing.

    Returns:
        {
            'status': 'success' | 'error',
            'response': str,
            'error_message': str,
            'error_stack': str,
        }
    """
    from datetime import datetime
    from django.utils import timezone
    from api.rag_service import get_rag_service
    from api.llm_service import create_event_discovery_prompt
    from api.llm_providers import get_llm_provider
    from api.llm_tools import AVAILABLE_TOOLS, ToolExecutor

    start_time = time.time()

    try:
        # 1. RAG retrieval
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

        context_events = rag_service.get_context_events(
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
            trace=recorder,
        )

        # 2. Prompt assembly
        current_datetime = timezone.localtime(timezone.now())
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
            conversation_history=settings.get('conversation_history'),
            user_preferences=settings.get('user_preferences'),
            trace=recorder,
        )

        # 3. LLM call (run async in event loop)
        import asyncio
        llm_provider = get_llm_provider()
        model_name = settings.get('model') or getattr(llm_provider, '_primary_model', 'deepseek-llm:7b')

        # Create tool executor for RAG searches (tools let LLM request more specific searches)
        use_tools = settings.get('use_tools', True)  # Enable by default
        tool_executor = ToolExecutor(rag_service, default_location=settings.get('location')) if use_tools else None
        tools = AVAILABLE_TOOLS if use_tools else None

        recorder.event('llm_request', {
            'model': model_name,
            'temperature': settings.get('temperature', 0.7),
            'prompt_chars': len(user_prompt),
            'system_chars': len(system_prompt),
            'tools_enabled': use_tools,
            'tools': [t['name'] for t in tools] if tools else [],
        })

        llm_start = time.time()

        async def get_llm_response():
            response = await llm_provider.generate_response(
                model=model_name,
                prompt=user_prompt,
                system_prompt=system_prompt,
                timeout_seconds=settings.get('timeout', 60),
                tools=tools,
                tool_executor=tool_executor,
            )
            return response

        # Run in a new event loop
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            response = loop.run_until_complete(get_llm_response())
        finally:
            loop.close()

        if response.success:
            full_response = response.response
        else:
            raise Exception(f"LLM error: {response.error}")

        llm_ms = int((time.time() - llm_start) * 1000)

        recorder.event('llm_response', {
            'full_response': full_response,
            'chars': len(full_response),
            'tokens_est': len(full_response) // 4,
        }, latency_ms=llm_ms)

        return {
            'status': 'success',
            'response': full_response,
        }

    except Exception as e:
        error_stack = traceback.format_exc()

        recorder.event('error', {
            'message': str(e),
            'stack': error_stack,
        })

        return {
            'status': 'error',
            'error_message': str(e),
            'error_stack': error_stack,
        }


@staff_member_required
@require_GET
def get_recent_runs(request):
    """
    Get recent debug runs for sidebar display.

    Query params:
    - status: Filter by status (success, error, pending, running)
    - limit: Max number of runs (default 20, max 50)

    Returns list of runs with settings for cloning.
    """
    status_filter = request.GET.get('status')
    limit = min(int(request.GET.get('limit', 20)), 50)

    runs = ChatDebugRun.objects.select_related('created_by')
    if status_filter and status_filter in ('pending', 'running', 'success', 'error'):
        runs = runs.filter(status=status_filter)
    runs = runs.order_by('-created_at')[:limit]

    return JsonResponse({
        'runs': [{
            'id': str(r.id),
            'request_text': r.request_text[:100] + ('...' if len(r.request_text) > 100 else ''),
            'status': r.status,
            'total_latency_ms': r.total_latency_ms,
            'created_at': r.created_at.isoformat(),
            'settings': r.settings,
        } for r in runs]
    })


@staff_member_required
@require_GET
def get_run_events(request, run_id):
    """
    Get all events for a run as JSON with full data (not truncated).

    Useful for displaying detailed trace information in the debug runner.
    """
    try:
        run_uuid = UUID(str(run_id))
        run = ChatDebugRun.objects.get(id=run_uuid)
    except (ValueError, ChatDebugRun.DoesNotExist):
        return HttpResponseBadRequest('Invalid run_id')

    events = list(run.events.order_by('seq').values('seq', 'stage', 'data', 'latency_ms'))

    return JsonResponse({
        'run_id': str(run.id),
        'request_text': run.request_text,
        'settings': run.settings,
        'status': run.status,
        'final_answer_text': run.final_answer_text,
        'total_latency_ms': run.total_latency_ms,
        'diagnostics': run.diagnostics,
        'events': events,
    })


@staff_member_required
@require_POST
def compare_runs_view(request):
    """
    Compare two debug runs side-by-side.

    POST body (JSON):
    {
        "run_id_a": "uuid",
        "run_id_b": "uuid"
    }

    Returns comparison data including settings diff, metrics diff, and event diff.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON')

    run_id_a = data.get('run_id_a')
    run_id_b = data.get('run_id_b')

    if not run_id_a or not run_id_b:
        return HttpResponseBadRequest('run_id_a and run_id_b are required')

    try:
        run_a = ChatDebugRun.objects.get(id=UUID(str(run_id_a)))
        run_b = ChatDebugRun.objects.get(id=UUID(str(run_id_b)))
    except (ValueError, ChatDebugRun.DoesNotExist) as e:
        return HttpResponseBadRequest(f'Invalid run_id: {e}')

    # Build run data dicts
    events_a = list(run_a.events.order_by('seq').values('seq', 'stage', 'data', 'latency_ms'))
    events_b = list(run_b.events.order_by('seq').values('seq', 'stage', 'data', 'latency_ms'))

    run_a_data = {
        'run_id': str(run_a.id),
        'request_text': run_a.request_text,
        'settings': run_a.settings,
        'status': run_a.status,
        'final_answer_text': run_a.final_answer_text,
        'total_latency_ms': run_a.total_latency_ms,
        'diagnostics': run_a.diagnostics,
        'events': events_a,
    }
    run_b_data = {
        'run_id': str(run_b.id),
        'request_text': run_b.request_text,
        'settings': run_b.settings,
        'status': run_b.status,
        'final_answer_text': run_b.final_answer_text,
        'total_latency_ms': run_b.total_latency_ms,
        'diagnostics': run_b.diagnostics,
        'events': events_b,
    }

    comparison = compare_run_results(run_a_data, run_b_data)

    return JsonResponse({
        'run_a': {
            'id': str(run_a.id),
            'request_text': run_a.request_text[:100],
            'status': run_a.status,
            'total_latency_ms': run_a.total_latency_ms,
        },
        'run_b': {
            'id': str(run_b.id),
            'request_text': run_b.request_text[:100],
            'status': run_b.status,
            'total_latency_ms': run_b.total_latency_ms,
        },
        'comparison': comparison,
        'responses': {
            'a': run_a.final_answer_text,
            'b': run_b.final_answer_text,
        }
    })


@staff_member_required
@require_POST
def create_variant_run(request):
    """
    Create a new run based on an existing run with modified settings.

    POST body (JSON):
    {
        "base_run_id": "uuid",
        "modified_settings": {
            "max_events": 30
        }
    }

    Returns the new run ID and stream URL.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON')

    base_run_id = data.get('base_run_id')
    modified_settings = data.get('modified_settings', {})

    if not base_run_id:
        return HttpResponseBadRequest('base_run_id is required')

    try:
        base_run = ChatDebugRun.objects.get(id=UUID(str(base_run_id)))
    except (ValueError, ChatDebugRun.DoesNotExist):
        return HttpResponseBadRequest('Invalid base_run_id')

    # Merge settings
    new_settings = {**base_run.settings, **modified_settings}

    # Create the new run
    new_run = ChatDebugRun.objects.create(
        created_by=request.user,
        request_text=base_run.request_text,
        settings=new_settings,
        status='pending',
    )

    return JsonResponse({
        'run_id': str(new_run.id),
        'stream_url': f'/admin/traces/api/run/{new_run.id}/stream/',
        'base_run_id': str(base_run.id),
        'modified_settings': modified_settings,
    })
