"""
FastAPI application for streaming chat functionality.
This runs as a separate service alongside Django.
"""
import os
import json
import asyncio
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
from ninja_jwt.tokens import UntypedToken
from ninja_jwt.exceptions import InvalidToken, TokenError

from events.models import Event
from api.llm_service import get_llm_service, create_event_discovery_prompt
from api.rag_service import get_rag_service
from . import debug_routes

app = FastAPI(
    title="Superschedules Chat Service",
    description="Streaming chat API with A/B testing for LLM responses",
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


async def verify_jwt_token(request: Request):
    """Verify JWT token from Authorization header"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    
    token = auth_header.split("Bearer ")[1]
    try:
        UntypedToken(token)
        return token
    except (InvalidToken, TokenError) as e:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "chat_service"}


@app.post("/chat/stream")
async def stream_chat(
    request: ChatRequest,
    token: str = Depends(verify_jwt_token)
):
    """
    Stream dual LLM responses for A/B testing.
    
    Returns Server-Sent Events (SSE) with incremental tokens from both models.
    """
    
    async def generate_stream():
        try:
            # Get LLM service
            llm_service = get_llm_service()
            
            # Get relevant events for context
            relevant_events = await get_relevant_events(request.message)
            
            if request.single_model_mode:
                # Single model mode - force DeepSeek (ignore frontend preference for now)
                model_name = llm_service.DEFAULT_MODEL_A  # Always use DeepSeek model
                model_id = "A"  # Always use A for single mode (the DeepSeek model)
                
                model_generator = stream_model_response(
                    llm_service, 
                    request.message, 
                    relevant_events,
                    model_name=model_name,
                    model_id=model_id
                )
                
                # Stream from single model
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
            model_name = llm_service.DEFAULT_MODEL_A if model_id == "A" else llm_service.DEFAULT_MODEL_B
        
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


@sync_to_async
def get_events_from_db():
    """Sync function to query Django models."""
    return list(Event.objects.all()[:3])

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
                similarity_threshold=0.3,  # Higher threshold for more relevant results
                time_filter_days=14  # Only show events in next 2 weeks
            )
        
        context_events = await loop.run_in_executor(None, run_rag_search)
        
        if context_events:
            print(f"RAG found {len(context_events)} relevant events")
            return context_events
        else:
            print("RAG found no relevant events, using fallback")
            # Fallback to recent events
            events = await get_events_from_db()
            return [
                {
                    'id': event.id,
                    'title': event.title,
                    'description': event.description,
                    'location': event.location,
                    'start_time': event.start_time.isoformat() if event.start_time else None,
                    'end_time': event.end_time.isoformat() if event.end_time else None,
                }
                for event in events
            ]
        
    except Exception as e:
        print(f"Error in RAG search: {e}")
        # Return fallback events
        return [
            {
                'id': 1,
                'title': 'Sample Event',
                'description': 'A sample event for testing',
                'location': 'Test Location',
                'start_time': datetime.now().isoformat(),
                'end_time': None,
            }
        ]


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
                print(f"Generator error: {data}")
            elif event_type == 'done':
                completed += 1
                
    finally:
        # Clean up tasks
        for task in tasks:
            if not task.done():
                task.cancel()
        
        # Wait for all tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)

