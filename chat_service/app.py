"""
FastAPI application for streaming chat functionality.
This runs as a separate service alongside Django.
"""
import os
import json
import asyncio
import logging
from typing import List, Dict, Any
from datetime import datetime, timezone

import django
from django.conf import settings
from asgiref.sync import sync_to_async

# Setup Django to use models and auth
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from ninja_jwt.tokens import AccessToken
from ninja_jwt.exceptions import InvalidToken, TokenError
from django.contrib.auth.models import User

from events.models import Event, ChatSession, ChatMessage
from api.llm_service import get_llm_service, create_event_discovery_prompt
from api.rag_service import get_rag_service
from . import debug_routes

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Superschedules Chat Service",
    description="Streaming chat API LLM responses",
    version="1.0.0"
)


@app.on_event("startup")
async def startup_event():
    """Warm up expensive services on startup to avoid cold-start latency."""
    import os

    # Only warm up if not in testing mode
    if os.environ.get("TESTING") != "1":
        logger.info("[STARTUP] Warming up RAG service...")
        try:
            from api.rag_service import warmup_rag_service
            # Run in thread pool to avoid blocking the event loop
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, warmup_rag_service)
            logger.info("[STARTUP] RAG service warmup complete")
        except Exception as e:
            logger.warning(f"[STARTUP] RAG warmup failed (non-fatal): {e}")


app.include_router(debug_routes.router)
# CORS middleware - allow production and development origins
allowed_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "https://www.eventzombie.com",
    "https://eventzombie.com",
    "https://admin.eventzombie.com",
    "https://api.eventzombie.com",
]

# Add ALB host dynamically if provided (for load balancer health checks)
if alb_host := os.environ.get('ALB_HOST'):
    allowed_origins.extend([f"http://{alb_host}", f"https://{alb_host}"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScoringWeightsRequest(BaseModel):
    """Configurable scoring weights for RAG ranking."""
    semantic_similarity: float = 0.4
    location_match: float = 0.25
    time_relevance: float = 0.20
    category_match: float = 0.10
    popularity: float = 0.05


class ChatRequest(BaseModel):
    message: str
    context: Dict[str, Any] = {}
    session_id: int | None = None  # Changed to int to match DB
    model_a: str | None = None
    model_b: str | None = None
    single_model_mode: bool = True  # Default to single model mode
    preferred_model: str | None = None  # Which model to use in single mode
    debug: bool = False  # Enable tracing for debugging (creates ChatDebugRun record)

    # Enhanced location handling - prefer location_id over string
    location_id: int | None = None  # Primary: Location table ID for deterministic filtering
    # context.location is still supported as fallback

    # Enhanced RAG configuration
    use_tiered_retrieval: bool = True  # Use new tiered retrieval with scoring
    max_recommended: int = 10  # Events to show prominently / pass to LLM
    max_additional: int = 15  # Secondary display events
    max_context: int = 50  # All events for map display
    scoring_weights: ScoringWeightsRequest | None = None  # Custom scoring weights


class EventMetadata(BaseModel):
    """Scoring breakdown for a returned event."""
    tier: str  # 'recommended', 'additional', 'context'
    final_score: float
    ranking_factors: Dict[str, Any]


class StreamChunk(BaseModel):
    model: str  # 'A' or 'B'
    token: str
    done: bool = False
    error: str | None = None
    error_code: str | None = None  # Machine-readable error code

    # Legacy field (kept for backward compatibility)
    suggested_event_ids: List[int] = []

    # Enhanced event response (tiered)
    recommended_event_ids: List[int] = []  # Top recommendations for LLM/display
    all_event_ids: List[int] = []  # Full set for map display
    new_event_ids: List[int] = []  # NEW this turn (for incremental enrichment)
    event_metadata: Dict[int, Dict[str, Any]] = {}  # id -> {tier, final_score, ranking_factors}

    follow_up_questions: List[str] = []
    response_time_ms: int | None = None
    session_id: int | None = None  # Return session ID to frontend


class JWTClaims(BaseModel):
    """JWT claims for downstream authorization"""
    model_config = {"arbitrary_types_allowed": True}
    
    user_id: int
    username: str
    exp: int
    iat: int
    token_type: str
    user: User | None = None  # Optional user object for authorization


async def verify_jwt_token(request: Request) -> JWTClaims:
    """
    Verify JWT AccessToken from Authorization header.
    
    Enforces:
    - Token signature validation  
    - Token type (access token only)
    - Expiration (exp) and not-before (nbf) claims (handled by AccessToken)
    - Optional audience (aud) and issuer (iss) validation if configured
    - Returns parsed claims for downstream authorization
    """
    from django.conf import settings
    
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    
    token_str = auth_header.split("Bearer ")[1]
    try:
        # Use AccessToken for proper validation (enforces exp/nbf/token_type automatically)
        access_token = AccessToken(token_str)
        
        # Extract validated claims
        claims = access_token.payload
        
        # Verify this is specifically an access token
        if claims.get('token_type') != 'access':
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        # Optional: Validate audience (aud) if configured
        expected_audience = getattr(settings, 'JWT_EXPECTED_AUDIENCE', None)
        if expected_audience:
            token_audience = claims.get('aud')
            if not token_audience or expected_audience not in (
                token_audience if isinstance(token_audience, list) else [token_audience]
            ):
                raise HTTPException(status_code=401, detail="Invalid token audience")
        
        # Optional: Validate issuer (iss) if configured
        expected_issuer = getattr(settings, 'JWT_EXPECTED_ISSUER', None)
        if expected_issuer:
            token_issuer = claims.get('iss')
            if token_issuer != expected_issuer:
                raise HTTPException(status_code=401, detail="Invalid token issuer")
        
        # Get user for authorization (optional, depending on needs)
        user = None
        try:
            from asgiref.sync import sync_to_async
            user = await sync_to_async(User.objects.get)(id=claims['user_id'])
        except User.DoesNotExist:
            # Log warning but don't fail - user might have been deleted
            logger.warning("User %d not found for valid JWT token", claims['user_id'])
        
        return JWTClaims(
            user_id=claims['user_id'],
            username=claims.get('username', ''),
            exp=claims['exp'],
            iat=claims['iat'],
            token_type=claims['token_type'],
            user=user
        )
        
    except (InvalidToken, TokenError) as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except KeyError as e:
        raise HTTPException(status_code=401, detail=f"Missing required claim: {str(e)}")


# =============================================================================
# Session Management Helpers
# =============================================================================

@sync_to_async
def get_or_create_session(user_id: int, session_id: int | None) -> ChatSession:
    """Get existing session or create new one."""
    if session_id:
        try:
            return ChatSession.objects.get(id=session_id, user_id=user_id)
        except ChatSession.DoesNotExist:
            logger.warning(f"Session {session_id} not found for user {user_id}, creating new")
    return ChatSession.objects.create(user_id=user_id)


@sync_to_async
def get_conversation_history(session: ChatSession, limit: int = 10) -> List[Dict]:
    """Get recent messages formatted for LLM context."""
    messages = session.get_recent_messages(limit=limit)
    return [{"role": msg.role, "content": msg.content} for msg in messages]


@sync_to_async
def save_message(session: ChatSession, role: str, content: str, metadata: dict = None, event_ids: List[int] = None) -> ChatMessage:
    """Save a message to the session."""
    msg = ChatMessage.objects.create(
        session=session,
        role=role,
        content=content,
        metadata=metadata or {}
    )
    if event_ids:
        msg.referenced_events.set(event_ids)

    # Auto-generate title from first user message
    if role == 'user' and not session.title:
        session.title = content[:50] + ("..." if len(content) > 50 else "")
        session.save(update_fields=['title', 'updated_at'])

    return msg


@app.get("/api/v1/chat/health")
async def health_check():
    """Health check endpoint with database and LLM connectivity tests"""
    from django.db import connection
    from asgiref.sync import sync_to_async
    from django.conf import settings

    health_status = {
        "status": "healthy",
        "service": "chat_service",
        "database": "unknown",
        "llm": "unknown",
        "llm_provider": {
            "env_var": os.environ.get("LLM_PROVIDER", "not set"),
            "settings": getattr(settings, "LLM_PROVIDER", "not set"),
            "provider_class": None,
        },
        "models": {}
    }
    
    # Test database connection
    try:
        @sync_to_async
        def test_db():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone()
        
        result = await test_db()
        if result and result[0] == 1:
            health_status["database"] = "connected"
        else:
            health_status["database"] = "query_failed"
            health_status["status"] = "unhealthy"
    except Exception as e:
        health_status["database"] = f"disconnected: {str(e)}"
        health_status["status"] = "unhealthy"
    
    # Test LLM connectivity and configured models
    try:
        llm_service = get_llm_service()
        health_status["llm_provider"]["provider_class"] = llm_service.__class__.__name__

        # Test if LLM provider is reachable
        available_models = await asyncio.wait_for(llm_service.get_available_models(), timeout=5)
        
        if available_models:
            health_status["llm"] = "connected"
            
            # Test configured models are available
            primary_model = llm_service.primary_model
            backup_model = llm_service.backup_model
            
            health_status["models"]["primary"] = {
                "name": primary_model,
                "available": primary_model in available_models
            }
            health_status["models"]["backup"] = {
                "name": backup_model,
                "available": backup_model in available_models
            }
            
            # Mark unhealthy only if primary model is missing
            if not health_status["models"]["primary"]["available"]:
                health_status["status"] = "unhealthy"
            elif not health_status["models"]["backup"]["available"]:
                health_status["status"] = "healthy"
                health_status["warning"] = "backup model unavailable"
                
        else:
            health_status["llm"] = "no_models"
            health_status["status"] = "unhealthy"
            
    except asyncio.TimeoutError:
        health_status["llm"] = "timeout"
        health_status["status"] = "unhealthy"
    except Exception as e:
        health_status["llm"] = f"error: {str(e)}"
        health_status["status"] = "unhealthy"
    
    return health_status


@app.post("/api/v1/chat/stream")
async def stream_chat(
    request: ChatRequest,
    jwt_claims: JWTClaims = Depends(verify_jwt_token)
):
    """
    Stream LLM responses with conversation memory.

    Returns Server-Sent Events (SSE) with incremental tokens.
    Stores messages in ChatSession for conversation history.
    """

    # Get or create session BEFORE the generator (so we have session_id)
    session = await get_or_create_session(jwt_claims.user_id, request.session_id)

    # Get conversation history for context
    conversation_history = await get_conversation_history(session, limit=10)

    # Save user message
    await save_message(session, 'user', request.message)

    # Create debug trace if requested
    trace = None
    debug_run_id = None
    if request.debug:
        from traces.models import ChatDebugRun
        from traces.recorder import TraceRecorder
        from asgiref.sync import sync_to_async

        # Create debug run record
        debug_run = await sync_to_async(ChatDebugRun.objects.create)(
            request_text=request.message,
            settings={
                'source': 'fastapi',
                'context': request.context,
                'session_id': request.session_id,
            },
            status='running',
        )
        debug_run_id = debug_run.id
        trace = TraceRecorder(run_id=debug_run.id, persist=True)
        logger.info(f"Debug mode enabled, trace ID: {debug_run.id}")

    async def generate_stream():
        from api.rag_service import RAGResult

        full_response = ""  # Track full response for saving
        response_time_ms = None
        model_name_used = None

        # Track event data for final response
        recommended_event_ids = []
        all_event_ids = []
        event_metadata = {}

        try:
            # Get LLM service
            llm_service = get_llm_service()

            # Quick health check - get available models (with timeout)
            try:
                available_models = await asyncio.wait_for(llm_service.get_available_models(), timeout=10)
                if not available_models:
                    logger.warning("No models available from Ollama")
                else:
                    logger.info("Ollama health check OK: %d models available", len(available_models))
            except asyncio.TimeoutError:
                logger.warning("Ollama health check timed out")
            except Exception as e:
                logger.warning("Ollama health check failed: %s", e)

            # Build scoring weights from request if provided
            scoring_weights_dict = None
            if request.scoring_weights:
                scoring_weights_dict = {
                    'semantic_similarity': request.scoring_weights.semantic_similarity,
                    'location_match': request.scoring_weights.location_match,
                    'time_relevance': request.scoring_weights.time_relevance,
                    'category_match': request.scoring_weights.category_match,
                    'popularity': request.scoring_weights.popularity,
                }

            # Get relevant events for context (using tiered retrieval if enabled)
            rag_result = await get_relevant_events(
                message=request.message,
                context=request.context,
                trace=trace,
                use_tiered=request.use_tiered_retrieval,
                location_id=request.location_id,
                max_recommended=request.max_recommended,
                max_additional=request.max_additional,
                max_context=request.max_context,
                scoring_weights=scoring_weights_dict,
            )

            # Handle both tiered and legacy results
            if isinstance(rag_result, RAGResult):
                # Tiered result - extract events for LLM (recommended only)
                relevant_events = [e.event_data for e in rag_result.recommended_events]

                # Populate event tracking for final response
                recommended_event_ids = rag_result.recommended_ids
                all_event_ids = rag_result.all_ids
                for ranked_event in rag_result.all_events:
                    event_metadata[ranked_event.event_data['id']] = {
                        'tier': ranked_event.tier,
                        'final_score': round(ranked_event.final_score, 3),
                        'ranking_factors': ranked_event.ranking_factors.to_dict(),
                    }
            else:
                # Legacy result - list of event dicts
                relevant_events = rag_result
                recommended_event_ids = [e['id'] for e in relevant_events[:10]]
                all_event_ids = [e['id'] for e in relevant_events]

            # Add conversation history to context for LLM prompt
            enhanced_context = {**request.context, 'chat_history': conversation_history}

            if request.single_model_mode:
                # Single model mode
                model_name_used = llm_service.primary_model
                model_id = "A"

                # Use retry mechanism for better reliability
                model_generator = stream_model_response_with_retry(
                    llm_service,
                    request.message,
                    relevant_events,
                    model_name=model_name_used,
                    model_id=model_id,
                    max_retries=1,
                    user_context=enhanced_context,
                    trace=trace  # Pass trace for debug events
                )

                # Stream from single model
                chunk_count = 0
                try:
                    async for chunk in model_generator:
                        chunk_count += 1
                        # Accumulate response for saving
                        if chunk.token:
                            full_response += chunk.token
                        if chunk.response_time_ms:
                            response_time_ms = chunk.response_time_ms

                        yield f"data: {json.dumps(chunk.dict())}\n\n"

                        if chunk_count % 50 == 0:
                            logger.debug("Streaming progress: %d chunks sent for %s", chunk_count, model_id)

                except Exception as e:
                    logger.error("Stream error after %d chunks for %s: %s: %s", chunk_count, model_id,
                               type(e).__name__, e)
                    error_chunk = StreamChunk(
                        model=model_id,
                        token="",
                        done=True,
                        error=f"Stream interrupted after {chunk_count} chunks: {str(e)}"
                    )
                    yield f"data: {json.dumps(error_chunk.dict())}\n\n"

            else:
                # A/B testing mode - use both models (simplified, no session tracking for A/B)
                model_a_generator = stream_model_response(
                    llm_service,
                    request.message,
                    relevant_events,
                    model_name=request.model_a,
                    model_id="A",
                    user_context=enhanced_context
                )

                model_b_generator = stream_model_response(
                    llm_service,
                    request.message,
                    relevant_events,
                    model_name=request.model_b,
                    model_id="B",
                    user_context=enhanced_context
                )

                async def stream_model(model_generator, model_id):
                    nonlocal full_response
                    try:
                        async for chunk in model_generator:
                            # Only track model A response for saving
                            if model_id == "A" and chunk.token:
                                full_response += chunk.token
                            yield f"data: {json.dumps(chunk.dict())}\n\n"
                    except Exception as e:
                        error_chunk = StreamChunk(
                            model=model_id,
                            token="",
                            done=True,
                            error=str(e)
                        )
                        yield f"data: {json.dumps(error_chunk.dict())}\n\n"

                model_a_stream = stream_model(model_a_generator, "A")
                model_b_stream = stream_model(model_b_generator, "B")

                async for item in merge_async_generators(model_a_stream, model_b_stream):
                    yield item

            # Save assistant response to session
            if full_response:
                await save_message(
                    session,
                    'assistant',
                    full_response,
                    metadata={'model': model_name_used, 'response_time_ms': response_time_ms},
                    event_ids=recommended_event_ids[:10] if recommended_event_ids else None
                )

            # Finalize debug trace if enabled
            if trace:
                from traces.diagnostics import compute_diagnostics

                diagnostics = compute_diagnostics(trace.events)

                # Use async finalize for proper DB transaction handling
                await trace.finalize_async(status='success', final_answer=full_response, diagnostics=diagnostics)
                logger.info(f"Debug trace finalized: {debug_run_id}")

            # Send final completion marker with session_id, event data, and debug_run_id
            final_data = {
                'model': 'SYSTEM',
                'token': '',
                'done': True,
                'session_id': session.id,
                # Legacy field for backward compatibility
                'suggested_event_ids': recommended_event_ids[:5],
                # Enhanced event response (tiered)
                'recommended_event_ids': recommended_event_ids,
                'all_event_ids': all_event_ids,
                'event_metadata': event_metadata,
            }
            if debug_run_id:
                final_data['debug_run_id'] = str(debug_run_id)
            yield f"data: {json.dumps(final_data)}\n\n"

        except Exception as e:
            # Finalize debug trace with error
            if trace:
                import traceback
                error_msg = str(e)
                error_stack = traceback.format_exc()

                # Use async finalize for proper DB transaction handling
                await trace.finalize_async(status='error', error_message=error_msg, error_stack=error_stack)

            error_chunk = StreamChunk(
                model="SYSTEM",
                token="",
                done=True,
                error=f"Stream error: {str(e)}",
                session_id=session.id
            )
            yield f"data: {json.dumps(error_chunk.dict())}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


async def stream_model_response(
    llm_service,
    message: str,
    context_events: List[Dict],
    model_name: str | None = None,
    model_id: str = "A",
    user_context: Dict[str, Any] = None,
    trace = None  # Optional TraceRecorder for debug tracing
):
    """
    Stream response from a single model using Ollama.
    This is a generator that yields StreamChunk objects.

    Args:
        llm_service: The LLM service instance
        message: User's message
        context_events: Events retrieved from RAG
        model_name: Model to use (defaults to primary/backup based on model_id)
        model_id: "A" or "B" for A/B testing
        user_context: Frontend context (location, date_range, preferences, chat_history)
        trace: Optional TraceRecorder for recording debug events
    """
    import time as time_module

    try:
        # Use default models if not specified
        if model_name is None:
            model_name = llm_service.primary_model if model_id == "A" else llm_service.backup_model

        # Extract location, preferences, and conversation history from user context
        location = None
        preferences = {}
        conversation_history = []
        if user_context:
            location = user_context.get('location')
            preferences = user_context.get('preferences', {})
            conversation_history = user_context.get('chat_history', [])

        # Create system and user prompts with full context
        current_time = datetime.now()
        system_prompt, user_prompt = create_event_discovery_prompt(
            message=message,
            events=context_events,
            context={
                'current_date': current_time.strftime('%A, %B %d, %Y at %I:%M %p'),
                'location': location,
            },
            conversation_history=conversation_history,
            user_preferences=preferences,
            # Don't pass trace here - we'll record events async below
        )

        # Record trace events using async methods for proper DB transaction handling
        if trace:
            # Record context blocks
            await trace.event_async('context_block', {
                'block_type': 'system_prompt',
                'text': system_prompt[:500] + '...' if len(system_prompt) > 500 else system_prompt,
                'chars': len(system_prompt),
                'tokens_est': len(system_prompt) // 4,
            })

            if context_events:
                events_text = f"[{len(context_events)} events provided to LLM]"
                await trace.event_async('context_block', {
                    'block_type': 'events',
                    'text': events_text,
                    'chars': len(events_text),
                    'event_count': len(context_events),
                })

            # Record final prompt
            await trace.event_async('prompt_final', {
                'system_prompt': system_prompt,
                'user_prompt': user_prompt[:1000] + '...' if len(user_prompt) > 1000 else user_prompt,
                'system_chars': len(system_prompt),
                'user_chars': len(user_prompt),
            })

            # Record LLM request event
            await trace.event_async('llm_request', {
                'model': model_name,
                'prompt_chars': len(user_prompt),
                'system_chars': len(system_prompt),
            })
        
        full_response = ""
        
        # Stream from Ollama
        async for chunk_data in llm_service.generate_streaming_response(
            model=model_name,
            prompt=user_prompt,
            system_prompt=system_prompt
        ):
            if not chunk_data['done']:
                # Stream individual tokens
                full_response += chunk_data['token']
                chunk = StreamChunk(
                    model=model_id,
                    token=chunk_data['token'],
                    done=False
                )
                yield chunk
            else:
                # Record LLM response event (async for proper DB handling)
                if trace:
                    await trace.event_async('llm_response', {
                        'full_response': full_response,
                        'chars': len(full_response),
                        'tokens_est': len(full_response) // 4,
                    }, latency_ms=chunk_data.get('response_time_ms'))

                # Final chunk with metadata
                final_chunk = StreamChunk(
                    model=model_id,
                    token="",
                    done=True,
                    suggested_event_ids=[event['id'] for event in context_events[:3]],
                    follow_up_questions=extract_follow_up_questions(full_response),
                    response_time_ms=chunk_data['response_time_ms']
                )

                if chunk_data.get('success', True):
                    yield final_chunk
                else:
                    error_chunk = StreamChunk(
                        model=model_id,
                        token="",
                        done=True,
                        error=chunk_data.get('error', 'Unknown error')
                    )
                    yield error_chunk

    except Exception as e:
        error_chunk = StreamChunk(
            model=model_id,
            token="",
            done=True,
            error=str(e)
        )
        yield error_chunk



async def get_relevant_events(
    message: str,
    context: Dict[str, Any] = None,
    trace=None,
    # New parameters for tiered retrieval
    use_tiered: bool = False,
    location_id: int | None = None,
    max_recommended: int = 10,
    max_additional: int = 15,
    max_context: int = 50,
    scoring_weights: Dict[str, float] | None = None,
):
    """
    Get relevant events for the message context using RAG.

    Args:
        message: User's search message
        context: Optional context with filters (location, date_range, is_virtual, user_location, etc.)
        trace: Optional TraceRecorder for debugging
        use_tiered: Use new tiered retrieval with multi-factor scoring
        location_id: Location table ID for deterministic filtering
        max_recommended: Max events in recommended tier
        max_additional: Max events in additional tier
        max_context: Max events in context tier
        scoring_weights: Custom scoring weights dict

    Returns:
        If use_tiered=False: List[Dict] (legacy format)
        If use_tiered=True: RAGResult with tiered events
    """
    from api.rag_service import ScoringWeights, RAGResult

    try:
        # Use RAG service for semantic search
        rag_service = get_rag_service()

        # Run RAG in thread since sentence transformers is CPU-bound
        loop = asyncio.get_event_loop()

        # Calculate time filter and extract filters from context
        time_filter_days = 14  # Default: 2 weeks
        date_from = None
        date_to = None
        location = None
        is_virtual = None
        max_distance_miles = None
        user_lat = None
        user_lng = None

        if context:
            # Extract location from context (fallback if no location_id)
            location = context.get('location')

            # Extract date range from context
            date_range = context.get('date_range')
            if date_range:
                date_from_str = date_range.get('from')
                date_to_str = date_range.get('to')
                if date_from_str:
                    date_from = datetime.fromisoformat(date_from_str).replace(tzinfo=timezone.utc)
                if date_to_str:
                    date_to = datetime.fromisoformat(date_to_str).replace(tzinfo=timezone.utc)
                # If explicit date range provided, don't use time_filter_days
                if date_from or date_to:
                    time_filter_days = None

            # Virtual/in-person filter
            is_virtual = context.get('is_virtual')  # True, False, or None

            # Geo-distance filter
            max_distance_miles = context.get('max_distance_miles')
            user_location = context.get('user_location')
            if user_location:
                user_lat = user_location.get('lat')
                user_lng = user_location.get('lng')

        if use_tiered:
            # Use new tiered retrieval with multi-factor scoring
            weights = ScoringWeights.from_dict(scoring_weights) if scoring_weights else None

            def run_tiered_search():
                logger.info(f"Tiered RAG: location_id={location_id}, weights={scoring_weights}")
                return rag_service.get_context_events_tiered(
                    user_message=message,
                    max_recommended=max_recommended,
                    max_additional=max_additional,
                    max_context=max_context,
                    scoring_weights=weights,
                    location_id=location_id,
                    location=location,
                    max_distance_miles=max_distance_miles,
                    user_lat=user_lat,
                    user_lng=user_lng,
                    time_filter_days=time_filter_days,
                    date_from=date_from,
                    date_to=date_to,
                    is_virtual=is_virtual,
                    trace=trace,
                )

            rag_result = await loop.run_in_executor(None, run_tiered_search)

            if rag_result.all_events:
                logger.info(f"Tiered RAG: {len(rag_result.recommended_events)} recommended, {len(rag_result.all_events)} total")
            else:
                logger.info("Tiered RAG: no events found")

            return rag_result

        else:
            # Legacy mode - use old get_context_events
            def run_rag_search():
                logger.info(f"Legacy RAG: date_from={date_from}, date_to={date_to}, time_filter_days={time_filter_days}")
                return rag_service.get_context_events(
                    user_message=message,
                    max_events=20,
                    similarity_threshold=0.2,
                    time_filter_days=time_filter_days,
                    date_from=date_from,
                    date_to=date_to,
                    location=location,
                    is_virtual=is_virtual,
                    max_distance_miles=max_distance_miles,
                    user_lat=user_lat,
                    user_lng=user_lng,
                    trace=trace,
                )

            context_events = await loop.run_in_executor(None, run_rag_search)

            if context_events:
                logger.info("RAG found %d relevant events", len(context_events))
                return context_events
            else:
                logger.info("RAG found no relevant events")
                return []

    except Exception as e:
        logger.error("Error in RAG search: %s", e)
        # Return empty result appropriate for mode
        if use_tiered:
            return RAGResult(total_considered=0, search_metadata={'error': str(e)})
        return []


async def stream_model_response_with_retry(
    llm_service,
    message: str,
    context_events: List[Dict],
    model_name: str | None = None,
    model_id: str = "A",
    max_retries: int = 2,
    user_context: Dict[str, Any] = None,
    trace = None  # Optional TraceRecorder for debug tracing
):
    """
    Stream response with retry logic for better reliability.
    """
    for attempt in range(max_retries + 1):
        try:
            chunks_received = 0
            stream_completed = False

            # Use original streaming function (only pass trace on first attempt to avoid duplicate events)
            async for chunk in stream_model_response(
                llm_service, message, context_events, model_name, model_id, user_context,
                trace=trace if attempt == 0 else None
            ):
                chunks_received += 1
                yield chunk
                
                # Check if this was a completion chunk
                if hasattr(chunk, 'done') and chunk.done:
                    stream_completed = True
                elif isinstance(chunk, dict) and chunk.get('done'):
                    stream_completed = True
                    
            # If we get here and stream completed successfully, we're done
            if stream_completed:
                return
            else:
                # Stream ended without completion - might be an issue
                raise Exception(f"Stream ended unexpectedly after {chunks_received} chunks")
                
        except Exception as e:
            if attempt < max_retries:
                logger.warning("Stream attempt %d failed for %s: %s", attempt + 1, model_id, e)
                logger.info("Retrying in 2 seconds... (%d/%d)", attempt + 1, max_retries)
                await asyncio.sleep(2)
                continue
            else:
                # Final attempt failed, yield error
                logger.error("All %d stream attempts failed for %s: %s", max_retries + 1, model_id, e)
                yield StreamChunk(
                    model=model_id,
                    token="",
                    done=True,
                    error=f"Stream failed after {max_retries + 1} attempts: {str(e)}"
                )
                return


def extract_follow_up_questions(response: str) -> List[str]:
    """Extract follow-up questions from response"""
    import re
    questions = re.findall(r'[^.!?]*\?', response)
    return [q.strip() for q in questions[:3]]


async def merge_async_generators(*generators):
    """Merge multiple async generators into a single stream."""
    import asyncio
    from typing import AsyncIterator
    
    async def collect_from_generator(gen, queue):
        """Collect items from a generator and put them in a queue."""
        try:
            async for item in gen:
                await queue.put(('item', item))
        except Exception as e:
            await queue.put(('error', str(e)))
        finally:
            await queue.put(('done', None))
    
    # Create a queue for collecting items from all generators
    queue = asyncio.Queue()
    
    # Start tasks for each generator
    tasks = [
        asyncio.create_task(collect_from_generator(gen, queue))
        for gen in generators
    ]
    
    completed = 0
    total_generators = len(generators)
    
    try:
        while completed < total_generators:
            event_type, data = await queue.get()
            
            if event_type == 'item':
                yield data
            elif event_type == 'error':
                # Handle generator error
                logger.error("Generator error: %s", data)
            elif event_type == 'done':
                completed += 1
                
    finally:
        # Clean up tasks
        for task in tasks:
            if not task.done():
                task.cancel()
        
        # Wait for all tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)



@app.post("/api/v1/chat")
async def chat_message(
    request: ChatRequest,
    jwt_claims: JWTClaims = Depends(verify_jwt_token)
):
    """
    Non-streaming chat endpoint that returns the full response.
    Collects all streaming chunks and returns them as a single response.
    """
    full_response = {
        "model": "A",
        "response": "",
        "suggested_event_ids": [],
        "follow_up_questions": []
    }

    try:
        # Get LLM service
        llm_service = get_llm_service()

        # Get relevant events for context (using frontend filters if provided)
        relevant_events = await get_relevant_events(request.message, request.context)

        # Use single model mode (default)
        model_name = llm_service.primary_model
        model_id = "A"

        # Stream and collect response
        model_generator = stream_model_response_with_retry(
            llm_service,
            request.message,
            relevant_events,
            model_name=model_name,
            model_id=model_id,
            max_retries=1,
            user_context=request.context
        )

        async for chunk in model_generator:
            if chunk.token:
                full_response["response"] += chunk.token
            if chunk.suggested_event_ids:
                full_response["suggested_event_ids"] = chunk.suggested_event_ids
            if chunk.follow_up_questions:
                full_response["follow_up_questions"] = chunk.follow_up_questions

        return full_response

    except Exception as e:
        logger.error("Error in non-streaming chat: %s", e)
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")


@app.get("/api/v1/chat/suggestions")
async def chat_suggestions(
    jwt_claims: JWTClaims = Depends(verify_jwt_token)
):
    """Get chat suggestions"""
    return {
        "suggestions": [
            "What's happening this weekend?",
            "Find me something to do tomorrow",
            "Show me free events nearby"
        ]
    }
