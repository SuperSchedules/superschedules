# Chat Session API & Service Update

## Overview
Implement conversation memory using the new ChatSession and ChatMessage models. This includes:
1. API endpoints for session management
2. Updating chat_service to store/retrieve messages
3. Passing new RAG filters (is_virtual, geo-distance)

## Models (Already Created)

```python
# events/models.py

class ChatSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_sessions")
    title = models.CharField(max_length=200, blank=True)  # Auto-generated from first message
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)  # Inactive = archived
    context = models.JSONField(default=dict, blank=True)  # Persistent: location, preferences

    def get_recent_messages(self, limit: int = 10):
        return list(self.messages.order_by('-created_at')[:limit])[::-1]


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = 'user', 'User'
        ASSISTANT = 'assistant', 'Assistant'
        SYSTEM = 'system', 'System'

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)  # Token counts, model used
    referenced_events = models.ManyToManyField(Event, blank=True, related_name="chat_references")
```

## Part 1: API Endpoints

Add to `api/views.py` or create new `api/chat_views.py`:

### 1.1 List User Sessions
```python
@router.get("/chat/sessions", response=List[ChatSessionSchema])
def list_sessions(request):
    """List user's chat sessions, most recent first."""
    sessions = ChatSession.objects.filter(
        user=request.user,
        is_active=True
    ).order_by('-updated_at')[:20]
    return sessions
```

### 1.2 Create Session
```python
@router.post("/chat/sessions", response=ChatSessionSchema)
def create_session(request, payload: CreateSessionSchema):
    """Create a new chat session."""
    session = ChatSession.objects.create(
        user=request.user,
        title=payload.title or "",
        context=payload.context or {}
    )
    return session
```

### 1.3 Get Session with Messages
```python
@router.get("/chat/sessions/{session_id}", response=ChatSessionDetailSchema)
def get_session(request, session_id: int):
    """Get session with recent messages."""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    messages = session.get_recent_messages(limit=50)
    return {
        "session": session,
        "messages": messages
    }
```

### 1.4 Archive Session
```python
@router.post("/chat/sessions/{session_id}/archive")
def archive_session(request, session_id: int):
    """Archive (soft-delete) a session."""
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    session.is_active = False
    session.save()
    return {"status": "archived"}
```

### Schemas
```python
class ChatSessionSchema(Schema):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    @staticmethod
    def resolve_message_count(obj):
        return obj.messages.count()


class ChatMessageSchema(Schema):
    id: int
    role: str
    content: str
    created_at: datetime
    metadata: dict


class ChatSessionDetailSchema(Schema):
    session: ChatSessionSchema
    messages: List[ChatMessageSchema]


class CreateSessionSchema(Schema):
    title: str = ""
    context: dict = {}
```

## Part 2: Update Chat Service

Modify `chat_service/app.py`:

### 2.1 Update ChatRequest model
```python
class ChatRequest(BaseModel):
    message: str
    context: Dict[str, Any] = {}
    session_id: int | None = None  # Changed from str to int
    # ... existing fields ...
```

### 2.2 Add session helper functions
```python
from events.models import ChatSession, ChatMessage

@sync_to_async
def get_or_create_session(user_id: int, session_id: int | None) -> ChatSession:
    """Get existing session or create new one."""
    if session_id:
        try:
            return ChatSession.objects.get(id=session_id, user_id=user_id)
        except ChatSession.DoesNotExist:
            pass
    return ChatSession.objects.create(user_id=user_id)


@sync_to_async
def get_conversation_history(session: ChatSession, limit: int = 10) -> List[Dict]:
    """Get recent messages formatted for LLM context."""
    messages = session.get_recent_messages(limit=limit)
    return [{"role": msg.role, "content": msg.content} for msg in messages]


@sync_to_async
def save_message(session: ChatSession, role: str, content: str, metadata: dict = None, event_ids: List[int] = None):
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
```

### 2.3 Update stream_chat endpoint
```python
@app.post("/api/v1/chat/stream")
async def stream_chat(request: ChatRequest, jwt_claims: JWTClaims = Depends(verify_jwt_token)):

    async def generate_stream():
        # Get or create session
        session = await get_or_create_session(jwt_claims.user_id, request.session_id)

        # Get conversation history
        conversation_history = await get_conversation_history(session, limit=10)

        # Save user message
        await save_message(session, 'user', request.message)

        # Get relevant events with NEW filters
        relevant_events = await get_relevant_events(request.message, request.context)

        # ... existing streaming code ...

        # After streaming complete, save assistant response
        await save_message(
            session,
            'assistant',
            full_response,
            metadata={'model': model_name, 'response_time_ms': response_time_ms},
            event_ids=[e['id'] for e in relevant_events[:5]]
        )

        # Include session_id in final chunk
        final_chunk = StreamChunk(
            model="SYSTEM",
            token="",
            done=True,
            session_id=session.id  # Add this field
        )
        yield f"data: {json.dumps(final_chunk.dict())}\n\n"

    return StreamingResponse(generate_stream(), ...)
```

### 2.4 Update get_relevant_events to use new filters
```python
async def get_relevant_events(message: str, context: Dict[str, Any] = None) -> List[Dict]:
    # ... existing code ...

    # NEW: Extract additional filters from context
    is_virtual = None
    max_distance_miles = None
    user_lat = None
    user_lng = None

    if context:
        # Virtual preference
        is_virtual = context.get('is_virtual')  # True, False, or None

        # Geo-distance
        max_distance_miles = context.get('max_distance_miles')
        user_location = context.get('user_location')
        if user_location:
            user_lat = user_location.get('lat')
            user_lng = user_location.get('lng')

    def run_rag_search():
        return rag_service.get_context_events(
            user_message=message,
            max_events=8,
            similarity_threshold=0.1,
            time_filter_days=time_filter_days,
            date_from=date_from,
            date_to=date_to,
            location=location,
            # NEW filters:
            is_virtual=is_virtual,
            max_distance_miles=max_distance_miles,
            user_lat=user_lat,
            user_lng=user_lng,
        )

    # ... rest of function ...
```

### 2.5 Update StreamChunk model
```python
class StreamChunk(BaseModel):
    model: str
    token: str
    done: bool = False
    error: str | None = None
    suggested_event_ids: List[int] = []
    follow_up_questions: List[str] = []
    response_time_ms: int | None = None
    session_id: int | None = None  # NEW: Return session ID to frontend
```

## Part 3: Update LLM Prompt Generation

In `stream_model_response()`, pass conversation history to prompt builder:

```python
async def stream_model_response(llm_service, message, context_events, ...):
    # ... existing code ...

    # Get conversation history from user_context (frontend sends this)
    # OR fetch from session if we have session object
    conversation_history = user_context.get('chat_history', []) if user_context else []
    preferences = user_context.get('preferences', {}) if user_context else {}

    system_prompt, user_prompt = create_event_discovery_prompt(
        message=message,
        events=context_events,
        context={
            'current_date': current_time.strftime('%A, %B %d, %Y at %I:%M %p'),
            'location': location,
        },
        conversation_history=conversation_history,  # NEW
        user_preferences=preferences,                # NEW
    )

    # ... rest of streaming code ...
```

## Migration

Run the migration that was already created:
```bash
cd ~/superschedules
source .venv/bin/activate
python manage.py migrate
```

## Testing

1. **Create session:**
```bash
curl -X POST http://localhost:8000/api/v1/chat/sessions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Test Session"}'
```

2. **Send message with session_id:**
```bash
curl -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What events are happening this weekend?",
    "session_id": 1,
    "context": {"location": "Newton"}
  }'
```

3. **Verify messages saved:**
```bash
curl http://localhost:8000/api/v1/chat/sessions/1 \
  -H "Authorization: Bearer $TOKEN"
```

## Frontend Integration Notes

After this is implemented, the frontend should:
1. Store returned `session_id` from first message
2. Pass `session_id` with subsequent messages in same conversation
3. Optionally fetch session list for "conversation history" UI
4. Can still send `chat_history` in context for redundancy (belt and suspenders)
