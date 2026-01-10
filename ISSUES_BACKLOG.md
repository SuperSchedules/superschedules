# Issues Backlog

This file contains detailed issue prompts for future work items. Each can be copy-pasted into a Claude Code session.

---

## Issue 4: Debug Runner Improvements - Errors Tab and Navigation

### Context
The debug runner at `/admin/traces/chatdebugrun/debug-runner/` allows testing chat queries and inspecting the pipeline. Currently, errors are collapsed in a fieldset and there are minimal links to related Django admin objects.

### Current State
- Location: `traces/admin.py` (ChatDebugRunAdmin), `templates/admin/traces/debug_runner.html`
- Errors stored in `ChatDebugRun.error_message` and `ChatDebugRun.error_stack`
- Events in `ChatDebugEvent` have a `stage` field that can be 'error'
- Debug runner loads run data via `/admin/traces/api/run/{id}/events/`

### Requirements

#### 1. Add Dedicated Errors Tab
- Add a new "Errors" tab to the debug runner tabs (after "Response Quality")
- Show error badge with count: `Errors (2)` in red if errors exist
- Tab content should show:
  - Main run error (if status='error'): error_message, truncated stack trace
  - All `ChatDebugEvent` records with `stage='error'`
  - For each error event: timestamp, error type, message, expandable stack trace
  - Severity classification if available

#### 2. Add Navigation Links
In the debug runner, add clickable links to:
- **Events**: Each retrieved event should link to `/admin/events/event/{id}/change/`
- **Location**: If location was resolved, link to `/admin/locations/location/{id}/change/`
- **Full Admin View**: Already exists, ensure it's prominent
- **Related ChatDebugEvents**: Link from summary to individual event admin pages
- In the retrieval table, add an "Admin" column with edit links

#### 3. Error Classification
Update `ChatDebugEvent` to capture:
```python
class ChatDebugEvent(models.Model):
    # ... existing fields ...
    error_type = models.CharField(max_length=50, null=True, blank=True)  # 'rag_error', 'llm_error', 'location_error', 'validation_error'
    error_severity = models.CharField(max_length=20, default='error')  # 'critical', 'warning', 'info'
```

Update `TraceRecorder.event()` to accept and store these fields when stage='error'.

#### 4. Error Tab JavaScript
Add to `debug_runner.html`:
```javascript
function renderErrors(events, run) {
    const errorTab = document.getElementById('tab-errors');
    const errorEvents = events.filter(e => e.stage === 'error');
    const hasRunError = run.status === 'error' && run.error_message;

    // Update error badge
    const errorCount = errorEvents.length + (hasRunError ? 1 : 0);
    const errorBadge = document.querySelector('[data-tab="errors"]');
    if (errorCount > 0) {
        errorBadge.innerHTML = `Errors <span style="background:#dc3545;color:white;padding:2px 6px;border-radius:10px;font-size:11px;margin-left:4px;">${errorCount}</span>`;
    }

    // Render error content...
}
```

### Testing
- Create a debug run that triggers an error (e.g., invalid location)
- Verify error tab shows the error with proper formatting
- Click event links and verify navigation works
- Test with both RAG errors and LLM errors

### Files to Modify
- `traces/models.py` - Add error_type, error_severity fields
- `traces/recorder.py` - Update event() to handle error fields
- `traces/views.py` - Include error fields in API responses
- `traces/admin.py` - Update admin display
- `templates/admin/traces/debug_runner.html` - Add errors tab and navigation links

---

## Issue 5: Elasticsearch Integration Layer

### Context
The RAG system currently uses pgvector for semantic search. We want to prepare for Elasticsearch as an optional candidate source, where ES provides a large result set that RAG then ranks/filters.

### Current State
- RAG service: `api/rag_service.py` (EventRAGService)
- Semantic search uses `pgvector` with cosine similarity
- No Elasticsearch index exists yet

### Requirements

#### 1. Search Service Abstraction
Create `api/search_service.py`:
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

@dataclass
class SearchResult:
    """Result from a search backend."""
    candidate_ids: List[int]        # Event IDs matching search
    search_handle: Optional[str]    # Opaque token for referring to this search
    total_hits: int                 # Total matches (may be > len(candidate_ids))
    facets: Dict[str, List[Any]]    # For filtering UI (category counts, etc.)
    search_time_ms: int

class EventSearchBackend(ABC):
    """Abstract interface for event search backends."""

    @abstractmethod
    def search(
        self,
        query: str,
        filters: Dict[str, Any],
        limit: int = 100,
    ) -> SearchResult:
        """Execute search and return candidate event IDs."""
        pass

    @abstractmethod
    def get_candidates_by_handle(self, search_handle: str) -> List[int]:
        """Retrieve cached search results by handle."""
        pass

class PgVectorSearchBackend(EventSearchBackend):
    """Search using existing pgvector semantic search."""
    # Wraps existing RAG semantic_search

class ElasticsearchBackend(EventSearchBackend):
    """Search using Elasticsearch index."""
    # Future implementation
```

#### 2. RAG Integration
Update `EventRAGService.get_context_events()`:
```python
def get_context_events(
    self,
    user_message: str,
    candidate_ids: List[int] | None = None,  # Restrict to these IDs
    search_handle: str | None = None,        # OR use cached search result
    max_events: int = 20,
    # ... existing params ...
) -> List[Dict[str, Any]]:
    # If candidate_ids or search_handle provided, restrict search
    if search_handle:
        candidate_ids = self._resolve_search_handle(search_handle)

    if candidate_ids:
        # Modify semantic_search to filter by IDs
        results = self.semantic_search_within_candidates(
            query=user_message,
            candidate_ids=candidate_ids,
            ...
        )
    else:
        # Use full corpus (current behavior)
        results = self.semantic_search(...)
```

#### 3. Search Handle Cache
Use Django cache for search result handles:
```python
from django.core.cache import cache

def create_search_handle(candidate_ids: List[int]) -> str:
    handle = str(uuid.uuid4())
    cache.set(f"search_handle:{handle}", candidate_ids, timeout=3600)  # 1 hour
    return handle

def resolve_search_handle(handle: str) -> List[int] | None:
    return cache.get(f"search_handle:{handle}")
```

#### 4. API Endpoints
Add to `api/views.py`:
```python
class SearchRequestSchema(Schema):
    query: str
    location_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    categories: List[str] = []
    is_virtual: bool | None = None
    limit: int = 100

class SearchResponseSchema(Schema):
    search_handle: str
    total_hits: int
    preview_events: List[EventSchema]  # First 10 for immediate display
    facets: dict

@router.post("/search", auth=JWTAuth())
def search_events(request, payload: SearchRequestSchema) -> SearchResponseSchema:
    """Search events and return handle for use with chat."""
    backend = get_search_backend()  # Factory pattern
    result = backend.search(query=payload.query, filters={...})
    return SearchResponseSchema(
        search_handle=result.search_handle,
        total_hits=result.total_hits,
        preview_events=Event.objects.filter(id__in=result.candidate_ids[:10]),
        facets=result.facets,
    )
```

#### 5. Chat Request Integration
Update `ChatRequest`:
```python
class ChatRequest(BaseModel):
    message: str
    search_handle: str | None = None  # Use this search result as candidates
    candidate_event_ids: List[int] | None = None  # Or explicit IDs
    # ... existing fields ...
```

#### 6. Configuration
Add to `config/settings.py`:
```python
# Search backend configuration
SEARCH_BACKEND = os.environ.get('SEARCH_BACKEND', 'pgvector')  # 'pgvector' or 'elasticsearch'
ELASTICSEARCH_URL = os.environ.get('ELASTICSEARCH_URL', 'http://localhost:9200')
ELASTICSEARCH_INDEX = os.environ.get('ELASTICSEARCH_INDEX', 'events')
```

### Testing
- Test pgvector backend with search_handle
- Test RAG with candidate_ids restriction
- Test cache expiry behavior
- Stub Elasticsearch backend for future use

### Files to Create/Modify
- `api/search_service.py` - New file with abstractions
- `api/rag_service.py` - Add candidate_ids support
- `api/views.py` - Add search endpoint
- `chat_service/app.py` - Handle search_handle in ChatRequest
- `config/settings.py` - Add configuration

---

## Issue 6: WebSocket Error Codes and Token Lifecycle

### Context
The frontend may leave connections open, leading to token expiry and generic "can't connect" errors. We need machine-readable error codes so the frontend can handle token refresh and reconnection properly.

### Current State
- Chat service: `chat_service/app.py` (FastAPI with SSE)
- JWT validation: `verify_jwt_token()` raises generic HTTPException
- No heartbeat mechanism
- Error responses use generic `error` string field

### Requirements

#### 1. Error Code Enum
Create `chat_service/errors.py`:
```python
from enum import Enum

class ChatErrorCode(str, Enum):
    # Authentication errors
    TOKEN_EXPIRED = "token_expired"
    TOKEN_INVALID = "token_invalid"
    AUTH_REQUIRED = "auth_required"
    AUTH_FAILED = "auth_failed"

    # Rate limiting
    RATE_LIMITED = "rate_limited"

    # Service errors
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_TIMEOUT = "llm_timeout"
    RAG_ERROR = "rag_error"

    # General
    SERVER_ERROR = "server_error"
    INVALID_REQUEST = "invalid_request"
```

#### 2. Update JWT Validation
Modify `verify_jwt_token()` in `chat_service/app.py`:
```python
from ninja_jwt.tokens import AccessToken
from ninja_jwt.exceptions import InvalidToken, TokenError
from jwt.exceptions import ExpiredSignatureError

async def verify_jwt_token(request: Request) -> JWTClaims:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"message": "Missing authorization header", "error_code": ChatErrorCode.AUTH_REQUIRED}
        )

    token_str = auth_header.split("Bearer ")[1]
    try:
        access_token = AccessToken(token_str)
        # ... validation logic ...
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"message": "Token has expired", "error_code": ChatErrorCode.TOKEN_EXPIRED}
        )
    except (InvalidToken, TokenError) as e:
        raise HTTPException(
            status_code=401,
            detail={"message": f"Invalid token: {str(e)}", "error_code": ChatErrorCode.TOKEN_INVALID}
        )
```

#### 3. Update StreamChunk
```python
class StreamChunk(BaseModel):
    model: str
    token: str
    done: bool = False
    error: str | None = None
    error_code: str | None = None  # NEW: ChatErrorCode value
    # ... existing fields ...
```

#### 4. Heartbeat/Ping Support
Add ping endpoint and in-stream pings:
```python
@app.get("/api/v1/chat/ping")
async def ping():
    """Lightweight heartbeat endpoint."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

# In streaming response, periodically send ping:
async def generate_stream():
    last_ping = time.time()
    # ... existing code ...

    # During long operations, send keepalive
    if time.time() - last_ping > 15:
        yield f"data: {json.dumps({'type': 'ping', 'timestamp': time.time()})}\n\n"
        last_ping = time.time()
```

#### 5. Token Refresh Endpoint
Add to `api/views.py` or create dedicated auth endpoint:
```python
@router.post("/auth/refresh-for-streaming", auth=JWTAuth())
def refresh_for_streaming(request):
    """
    Quick token refresh check before opening SSE connection.
    Returns current token validity and time remaining.
    """
    from ninja_jwt.tokens import AccessToken

    # Token is already validated by JWTAuth, just return status
    return {
        "valid": True,
        "user_id": request.user.id,
        "token_expires_in_seconds": 300,  # Approximate
        "should_refresh": False,  # Could check if close to expiry
    }
```

#### 6. Frontend Contract Documentation
Create `docs/WEBSOCKET_CONTRACT.md`:
```markdown
# Chat SSE Connection Contract

## Error Codes

| Code | HTTP Status | Meaning | Client Action |
|------|-------------|---------|---------------|
| `token_expired` | 401 | JWT has expired | Refresh token, reconnect |
| `token_invalid` | 401 | JWT is malformed/invalid | Re-authenticate |
| `auth_required` | 401 | No token provided | Prompt login |
| `rate_limited` | 429 | Too many requests | Wait and retry |
| `llm_unavailable` | 503 | LLM service down | Show error, retry |
| `server_error` | 500 | Internal error | Show error, retry |

## Recommended Client Flow

1. Before opening SSE:
   - Check token expiry locally
   - If close to expiry, call `/api/v1/token/refresh/`

2. On connection error:
   - Parse error response for `error_code`
   - If `token_expired`: refresh token, retry connection
   - If `token_invalid`: redirect to login
   - If `rate_limited`: show message, exponential backoff
   - If `llm_unavailable` or `server_error`: show error, retry with backoff

3. During streaming:
   - Handle `ping` events silently
   - On stream error chunk: check `error_code` field
   - If connection drops unexpectedly: attempt reconnect with fresh token

## Keepalive

- Server sends `{"type": "ping"}` every 15 seconds during active streams
- No client response required (SSE is unidirectional)
- If no data received for 60 seconds, consider connection stale
```

### Testing
- Test expired token returns `token_expired` error code
- Test invalid token returns `token_invalid` error code
- Test ping endpoint responds correctly
- Test keepalive pings during long LLM responses

### Files to Create/Modify
- `chat_service/errors.py` - New file with error codes
- `chat_service/app.py` - Update JWT validation, add ping, update StreamChunk
- `api/views.py` - Add refresh-for-streaming endpoint
- `docs/WEBSOCKET_CONTRACT.md` - New documentation file

---
