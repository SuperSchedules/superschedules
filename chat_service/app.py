"""
FastAPI application for streaming chat functionality.
This runs as a separate service alongside Django.
"""
import os
import json
import asyncio
import logging
from typing import List, Dict, Any
from datetime import datetime

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

from events.models import Event
from api.llm_service import get_llm_service, create_event_discovery_prompt
from api.rag_service import get_rag_service
from . import debug_routes

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Superschedules Chat Service",
    description="Streaming chat API LLM responses",
    version="1.0.0"
)

app.include_router(debug_routes.router)
# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Frontend URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    context: Dict[str, Any] = {}
    session_id: str | None = None
    model_a: str | None = None
    model_b: str | None = None
    single_model_mode: bool = True  # Default to single model mode
    preferred_model: str | None = None  # Which model to use in single mode


class StreamChunk(BaseModel):
    model: str  # 'A' or 'B'
    token: str
    done: bool = False
    error: str | None = None
    suggested_event_ids: List[int] = []
    follow_up_questions: List[str] = []
    response_time_ms: int | None = None


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


@app.get("/health")
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


@app.post("/chat/stream")
async def stream_chat(
    request: ChatRequest,
    jwt_claims: JWTClaims = Depends(verify_jwt_token)
):
    """
    Stream dual LLM responses for A/B testing.
    
    Returns Server-Sent Events (SSE) with incremental tokens from both models.
    """
    
    async def generate_stream():
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
            
            # Get relevant events for context
            relevant_events = await get_relevant_events(request.message)
            
            if request.single_model_mode:
                # Single model mode - force DeepSeek (ignore frontend preference for now)
                model_name = llm_service.primary_model  # Always use primary model
                model_id = "A"  # Always use A for single mode (the DeepSeek model)
                
                # Use retry mechanism for better reliability
                model_generator = stream_model_response_with_retry(
                    llm_service, 
                    request.message, 
                    relevant_events,
                    model_name=model_name,
                    model_id=model_id,
                    max_retries=1  # One retry for better responsiveness
                )
                
                # Stream from single model
                chunk_count = 0
                try:
                    async for chunk in model_generator:
                        chunk_count += 1
                        yield f"data: {json.dumps(chunk.dict())}\n\n"
                        
                        # Log progress periodically
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
                # A/B testing mode - use both models
                model_a_generator = stream_model_response(
                    llm_service, 
                    request.message, 
                    relevant_events,
                    model_name=request.model_a,
                    model_id="A"
                )
                
                model_b_generator = stream_model_response(
                    llm_service, 
                    request.message, 
                    relevant_events,
                    model_name=request.model_b,
                    model_id="B"
                )
                
                # Stream responses from both models concurrently
                async def stream_model(model_generator, model_id):
                    try:
                        async for chunk in model_generator:
                            yield f"data: {json.dumps(chunk.dict())}\n\n"
                    except Exception as e:
                        error_chunk = StreamChunk(
                            model=model_id, 
                            token="", 
                            done=True, 
                            error=str(e)
                        )
                        yield f"data: {json.dumps(error_chunk.dict())}\n\n"
                
                # Create async generators for both models
                model_a_stream = stream_model(model_a_generator, "A")
                model_b_stream = stream_model(model_b_generator, "B")
                
                # Stream from both models concurrently
                async for item in merge_async_generators(model_a_stream, model_b_stream):
                    yield item
            
            # Send final completion marker
            final_chunk = StreamChunk(
                model="SYSTEM",
                token="",
                done=True
            )
            yield f"data: {json.dumps(final_chunk.dict())}\n\n"
            
        except Exception as e:
            error_chunk = StreamChunk(
                model="SYSTEM",
                token="",
                done=True,
                error=f"Stream error: {str(e)}"
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
    model_id: str = "A"
):
    """
    Stream response from a single model using Ollama.
    This is a generator that yields StreamChunk objects.
    """
    try:
        # Use default models if not specified
        if model_name is None:
            model_name = llm_service.primary_model if model_id == "A" else llm_service.backup_model
        
        # Create system and user prompts
        current_time = datetime.now()
        system_prompt, user_prompt = create_event_discovery_prompt(
            message, context_events, {
                'current_date': current_time.strftime('%A, %B %d, %Y at %I:%M %p'),
                'location': None,
                'preferences': {}
            }
        )
        
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



async def get_relevant_events(message: str) -> List[Dict]:
    """
    Get relevant events for the message context using RAG.
    """
    try:
        # Use RAG service for semantic search
        rag_service = get_rag_service()
        
        # Run RAG in thread since sentence transformers is CPU-bound
        import asyncio
        loop = asyncio.get_event_loop()
        
        def run_rag_search():
            return rag_service.get_context_events(
                user_message=message,
                max_events=8,
                similarity_threshold=0.1,  # Higher threshold for more relevant results
                time_filter_days=14  # Only show events in next 2 weeks
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
        return []


async def stream_model_response_with_retry(
    llm_service, 
    message: str, 
    context_events: List[Dict],
    model_name: str | None = None,
    model_id: str = "A",
    max_retries: int = 2
):
    """
    Stream response with retry logic for better reliability.
    """
    for attempt in range(max_retries + 1):
        try:
            chunks_received = 0
            stream_completed = False
            
            # Use original streaming function
            async for chunk in stream_model_response(
                llm_service, message, context_events, model_name, model_id
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

