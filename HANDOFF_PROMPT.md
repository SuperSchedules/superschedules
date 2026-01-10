# EventZombie Backend Continuation Prompt

You are a senior backend/Django engineer working on EventZombie, an LLM-based local events chat app.

## Tech Context
- **Backend**: Django (repo: superschedules) with RAG-style retrieval for events
- **Chat Service**: FastAPI with SSE streaming (port 8002)
- **Admin/debug**: Django admin at https://admin.eventzombie.com
- **Frontend**: Separate SPA (repo: superschedules_frontend)
- **Embedding model**: sentence-transformers (all-MiniLM-L6-v2), runs locally on GPU

## Recently Completed Work

### 1. Enhanced RAG with Tiered Events and Multi-Factor Scoring
**Files**: `api/rag_service.py`

- Added `ScoringWeights`, `RankingFactors`, `RankedEvent`, `RAGResult` dataclasses
- Implemented `get_context_events_tiered()` with configurable scoring:
  - `semantic_similarity` (default 0.40)
  - `location_match` (default 0.25)
  - `time_relevance` (default 0.20)
  - `category_match` (default 0.10)
  - `popularity` (default 0.05)
- Events are now tiered: `recommended`, `additional`, `context`

### 2. Location ID-Based API
- Chat API now accepts `location_id` (int) for deterministic geo-filtering
- Falls back to string-based location resolution if no ID provided
- Location suggest endpoint already exists at `/api/v1/locations/suggest`

### 3. Updated Chat API Response Schema
**Files**: `chat_service/app.py`

New fields in `ChatRequest`:
- `location_id`, `use_tiered_retrieval`, `max_recommended`, `max_additional`, `max_context`, `scoring_weights`

New fields in final `StreamChunk`:
- `recommended_event_ids`, `all_event_ids`, `event_metadata` (with tier, final_score, ranking_factors)

### 4. Debug Runner RAG Parameter Controls
**Files**: `templates/admin/traces/debug_runner.html`, `traces/views.py`

- Added collapsible "Scoring Weights (Advanced)" section with sliders
- Added tier configuration dropdowns
- Enhanced retrieval table with tier badges, scores, and admin links

### 5. Fixed Debug Trace Bugs
**Files**: `chat_service/app.py`, `traces/recorder.py`

**Bug 1**: Debug traces stuck at "running" status
- Root cause: `trace.finalize()` used sync Django ORM in async context
- Fix: Added `TraceRecorder.event_async()` and `finalize_async()` methods

**Bug 2**: Missing trace events (context_block, prompt_final, llm_request, llm_response)
- Root cause: Same sync/async transaction isolation issue
- Fix: Updated `stream_model_response()` to use async trace methods

### 6. RAG Performance Optimization
**Files**: `api/rag_service.py`, `chat_service/app.py`

**Problem**: 4+ second latency on first request (model loading + GPU warmup)

**Solution**:
- Added `warmup()` method and `warmup_rag_service()` function
- Added embedding cache (`_embedding_cache`) for repeated queries
- Added FastAPI startup event to warm up on deploy
- Added detailed performance logging (`[RAG PERF]`)

**Results**:
| Scenario | Before | After |
|----------|--------|-------|
| Cold start | 4340ms | N/A (warmup at startup) |
| Normal query | 4340ms | 6-15ms |
| Cache hit | N/A | 1.5ms |

### 7. Documentation Updates
- Added "Django ORM in Async Contexts" section to `CLAUDE.md`
- Created `docs/CHAT_API.md` with full API documentation
- Created `ISSUES_BACKLOG.md` with detailed prompts for remaining work

---

## Remaining Work Items

### Priority 1: Embedding Microservice (NEW)

**Problem**: Each FastAPI worker loads its own copy of the sentence-transformers model (~500MB+ each). With multiple workers, this wastes memory and causes cold starts per worker.

**Proposed Solution**: Create a dedicated embedding microservice:
1. New `embedding_service/` directory with a simple FastAPI app
2. Single endpoint: `POST /embed` that takes text and returns 384-dim vector
3. Runs as single worker, always warm
4. Update `EventRAGService` to call this service via HTTP instead of loading model locally

**Benefits**:
- Single model instance regardless of chat worker count
- Can scale embedding service independently
- Easier to swap embedding models later

### Priority 2: Debug Runner Improvements
**Details in**: `ISSUES_BACKLOG.md` (Issue 4)

- Add dedicated "Errors" tab with error count badge
- Add navigation links to related admin objects (events, locations, traces)
- Add error classification fields to `ChatDebugEvent` model
- Update retrieval table with "Admin" column links

### Priority 3: Elasticsearch Integration Layer
**Details in**: `ISSUES_BACKLOG.md` (Issue 5)

- Create `api/search_service.py` with `EventSearchBackend` abstraction
- Implement `PgVectorSearchBackend` (wraps current RAG)
- Stub `ElasticsearchBackend` for future use
- Add search handle caching for passing candidate sets to RAG
- Add `/api/search` endpoint that returns search handle
- Update `ChatRequest` to accept `search_handle` or `candidate_event_ids`

### Priority 4: WebSocket Error Codes and Token Lifecycle
**Details in**: `ISSUES_BACKLOG.md` (Issue 6)

- Create `chat_service/errors.py` with `ChatErrorCode` enum
- Update JWT validation to return machine-readable error codes
- Add `error_code` field to `StreamChunk`
- Add ping endpoint and in-stream keepalive pings
- Create `docs/WEBSOCKET_CONTRACT.md` with client contract

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `api/rag_service.py` | RAG retrieval, scoring, embedding |
| `chat_service/app.py` | FastAPI streaming chat endpoint |
| `traces/recorder.py` | Debug trace recording (sync + async) |
| `traces/views.py` | Debug runner API endpoints |
| `templates/admin/traces/debug_runner.html` | Debug runner UI |
| `ISSUES_BACKLOG.md` | Detailed prompts for items 4-6 |
| `docs/CHAT_API.md` | Chat API documentation |

## Important Patterns

### Django ORM in Async Contexts (FastAPI)
When calling Django ORM from async code, wrap in `sync_to_async`:
```python
from asgiref.sync import sync_to_async

@sync_to_async
def do_db_work():
    return MyModel.objects.create(...)

await do_db_work()
```

The `TraceRecorder` class provides both sync and async methods:
- `trace.event()` / `trace.event_async()`
- `trace.finalize()` / `trace.finalize_async()`

### Running Tests
```bash
# Django tests
source .venv/bin/activate && python manage.py test --settings=config.test_settings --buffer

# FastAPI tests
source .venv/bin/activate && DJANGO_SETTINGS_MODULE=config.test_settings pytest chat_service/tests/ -v
```

---

## Suggested Starting Point

Start with the **Embedding Microservice** (Priority 1) since it's a clean, isolated piece of work that improves infrastructure. The implementation would be:

1. Create `embedding_service/app.py` - minimal FastAPI with `/embed` endpoint
2. Create `embedding_service/Dockerfile` for deployment
3. Update `api/rag_service.py` to use HTTP client instead of local model
4. Add configuration for embedding service URL
5. Update docker-compose for local development
