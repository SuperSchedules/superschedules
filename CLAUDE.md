# Superschedules

AI-powered local events discovery platform. Users ask: *"Activities for 3-5 year olds in Newton, next 3 hours"* and get intelligent recommendations via RAG-powered chat.

## Key Capabilities

- **RAG-Powered Event Search** - Semantic search using sentence-transformers + pgvector
- **Location Resolution** - Deterministic city/town resolution using Census Gazetteer (~31K US places)
- **Venue Normalization** - Address-based deduplication, geocoding, Schema.org enrichment
- **Venue-First Architecture** - Venues own event calendar URLs, events belong to venues
- **OSM Integration** - Navigator syncs POIs from OpenStreetMap
- **Streaming Chat** - FastAPI SSE streaming with RAG context injection
- **Production Deployment** - Terraform-managed AWS infrastructure

## Core Data Model

**Venue-First Architecture**: Venue is the first-class citizen.
- `Venue.events_urls` - JSONField array of calendar URLs to scrape (from Navigator)
- `Event.venue` - Required FK, events deduplicated by `(venue, external_id)`
- `ScrapingJob.venue` - Jobs link directly to venues

**Location Resolution** (`locations/` app):
- `Location` model - Canonical US cities/towns with lat/lng from Census Gazetteer
- `resolve_location("Newton, MA")` → coordinates + confidence
- Bounding box + Haversine distance filtering (10-mile default radius)

## Multi-Repository Architecture

This is the **main backend repository** in a 5-repo system:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Superschedules Platform                      │
├─────────────────────────────────────────────────────────────────┤
│ superschedules_frontend (React SPA)                             │
│   └─> Chat interface with streaming LLM responses               │
│                           ↓                                      │
│ superschedules (THIS REPO - Django + FastAPI)                   │
│   ├─> Django API (Port 8000): Auth, Events, Admin, RAG         │
│   └─> FastAPI Chat (Port 8002): Streaming chat with Deepseek   │
│                           ↓                                      │
│ superschedules_collector (Port 8001)                            │
│   └─> Parses event pages, follows pagination                    │
│                           ↓                                      │
│ superschedules_navigator                                         │
│   └─> Scans sites to discover event pages                       │
│                           ↓                                      │
│ superschedules_IAC (Terraform)                                   │
│   └─> Infrastructure as Code for AWS deployment                 │
└─────────────────────────────────────────────────────────────────┘
```

## Tech Stack

### Backend (This Repo)
- **Framework**: Django 5.0 with Django Ninja API
- **Chat Service**: FastAPI with Server-Sent Events (SSE) streaming
- **Database**: PostgreSQL 15+ with pgvector extension
- **LLM**: Ollama running Deepseek (local development)
- **RAG**: sentence-transformers (all-MiniLM-L6-v2) + pgvector cosine similarity
- **Auth**: JWT tokens via ninja-jwt
- **Testing**: Django test runner (pgvector-aware) + pytest for FastAPI
- **Admin**: Grappelli interface

### Data Models
- **Event**: Title, description, venue FK (required), room_name, start/end times, vector embedding
- **Venue**: First-class citizen with structured address, events_urls (calendar URLs), geocoding, OSM data
- **Location**: Canonical US cities/towns with lat/lng, population (from Census Gazetteer)
- **SiteStrategy**: Domain-specific scraping patterns and success rates
- **ScrapingJob**: Async job tracking with venue FK for collector integration

## Development Guidelines

See `SHARED_GUIDELINES.md` for common guidelines (code style, commits, security, AI assistant rules).

### Test-Driven Development (TDD)
**IMPORTANT: Always start with tests when adding new features:**
1. Write failing tests first
2. Implement minimal code to make tests pass
3. Refactor while keeping tests green
4. Run test suite: `python manage.py test --settings=config.test_settings --buffer`
   - `--buffer` flag suppresses stdout/stderr for clean output
   - Use `LOG_LEVEL=INFO` to see logs during debugging

### Code Style & Naming
- Python 3.11+, PEP 8, 4-space indentation
- Type hints and docstrings for all public functions
- Django apps: lowercase (`events`, `api`), models: `CamelCase`, functions/variables: `snake_case`
- Keep views thin; place LLM/RAG logic in `api/` services
- Avoid circular imports

### Django ORM in Async Contexts (FastAPI)
**CRITICAL:** When calling Django ORM from FastAPI async code (generators, coroutines), you MUST wrap synchronous database calls in `sync_to_async`. Otherwise, database operations may silently fail due to transaction isolation issues.

**Problem:**
```python
# BAD - This will silently fail or have transaction issues
async def my_async_function():
    MyModel.objects.create(...)  # Sync ORM in async context
    MyModel.objects.filter(...).update(...)  # Also broken
```

**Solution:**
```python
from asgiref.sync import sync_to_async

# GOOD - Wrap in sync_to_async
async def my_async_function():
    @sync_to_async
    def do_db_work():
        return MyModel.objects.create(...)

    result = await do_db_work()
```

**Real example from this codebase:** The `TraceRecorder` class provides both sync and async methods:
- `trace.event()` - Use from sync code (Django views, management commands)
- `trace.event_async()` - Use from async code (FastAPI endpoints, async generators)
- `trace.finalize()` vs `trace.finalize_async()` - Same pattern

This applies to ALL Django ORM operations in FastAPI code, including:
- Model.objects.create/update/delete
- Model.objects.filter().update()
- QuerySet operations
- Any code that touches the database

## Project Structure

```
superschedules/
├── config/              # Django settings, URLs, WSGI
├── events/              # Core Django app
│   ├── models.py       # Event, Source, SiteStrategy, ScrapingJob
│   ├── admin.py        # Grappelli admin with collector integration
│   ├── tests/          # Event model and API tests
│   └── management/commands/
│       └── update_embeddings.py  # RAG embedding management
├── venues/              # Venue normalization
│   ├── models.py       # Venue model with structured address
│   ├── extraction.py   # Venue extraction from collector data
│   └── geocoding.py    # Nominatim geocoding service
├── locations/           # Location resolution (NEW)
│   ├── models.py       # Location model (Census Gazetteer data)
│   ├── services.py     # resolve_location(), filter_by_distance()
│   ├── admin.py        # Location admin interface
│   ├── tests/          # 57 tests for normalization, resolution, geo-filter
│   └── management/commands/
│       └── import_locations.py  # Census data import
├── api/                 # Service layer
│   ├── views.py        # Django Ninja API endpoints
│   ├── llm_service.py  # Ollama/Deepseek integration
│   ├── rag_service.py  # Semantic search + location resolution
│   ├── auth.py         # JWT + service token authentication
│   └── tests/          # API, RAG, LLM tests
├── chat_service/        # FastAPI streaming chat
│   ├── app.py          # SSE streaming endpoints with RAG context
│   └── tests/          # FastAPI integration tests
├── scripts/             # Utility scripts
├── requirements.txt     # Python dependencies
└── manage.py           # Django management
```

## Key Components

### 1. RAG System (Implemented)
**Location:** `api/rag_service.py`
- Semantic search using sentence-transformers (all-MiniLM-L6-v2)
- Vector embeddings stored in PostgreSQL with pgvector
- Cosine similarity for event matching
- Real-time context retrieval for chat responses
- **Deterministic location resolution** before semantic search
- Embeddings auto-generated via Django signals on Event save

**Usage:**
```python
from api.rag_service import get_rag_service

rag = get_rag_service()
results = rag.search_events("activities for kids in Newton", limit=5)
```

### 2. Location Resolution System (NEW)
**Location:** `locations/services.py`
- Deterministic city/town → lat/lng resolution (no LLM needed)
- US Census Gazetteer data (~31K places with coordinates + population)
- Disambiguation: prefers Greater Boston states (MA, NH, RI, CT, ME, VT)
- Bounding box + Haversine distance filtering (10-mile default radius)

**Usage:**
```python
from locations.services import resolve_location, filter_by_distance

# Resolve "Newton" or "Newton, MA" to coordinates
result = resolve_location("Newton, MA")
print(result.matched_location)  # Newton, MA
print(result.latitude, result.longitude)  # 42.337807, -71.209182
print(result.confidence)  # 1.0 (exact match)
print(result.is_ambiguous)  # False

# Filter events by distance
from events.models import Event
events = filter_by_distance(Event.objects.all(), lat=42.34, lng=-71.21, radius_miles=10)
```

**Data Import:**
```bash
# Import Census Gazetteer data (production)
python manage.py import_locations --source=census

# Import specific state only
python manage.py import_locations --source=census --state=MA

# Dry run to preview
python manage.py import_locations --dry-run --limit=100
```

### 3. Enhanced RAG with Tiered Retrieval (NEW)
**Location:** `api/rag_service.py`

The RAG service now supports tiered retrieval with multi-factor scoring:

**Scoring Factors:**
- `semantic_similarity` (default 0.40) - Embedding cosine similarity
- `location_match` (default 0.25) - Distance-based scoring (closer = higher)
- `time_relevance` (default 0.20) - Sooner events score higher
- `category_match` (default 0.10) - Tag/audience overlap with query
- `popularity` (default 0.05) - Source quality signals

**Tiered Results:**
- `recommended`: Top 5-10 events for LLM context (high confidence)
- `additional`: Next 10-20 events for secondary display
- `context`: Up to 50 more events for map display

**Usage:**
```python
from api.rag_service import get_rag_service, ScoringWeights

rag = get_rag_service()

# Custom weights emphasizing location
weights = ScoringWeights(
    semantic_similarity=0.3,
    location_match=0.4,  # More weight on location
    time_relevance=0.2,
    category_match=0.05,
    popularity=0.05,
)

result = rag.get_context_events_tiered(
    user_message="activities for kids",
    location_id=123,  # Location table ID (preferred)
    scoring_weights=weights,
    max_recommended=10,
    max_additional=15,
    max_context=50,
)

# Access tiered results
for event in result.recommended_events:
    print(f"{event.tier}: {event.event_data['title']} (score: {event.final_score:.2f})")
    print(f"  Factors: {event.ranking_factors.to_dict()}")
```

### 4. LLM Integration
**Location:** `api/llm_service.py`
- Ollama integration with Deepseek model
- Streaming responses via FastAPI SSE
- Context-aware prompts with RAG results
- Fallback model support (llama3.2:3b)

### 5. Event Collection Pipeline
**Flow:** Navigator → Collector → Django API
1. **Navigator** scans sites to find event pages
2. **Collector** (port 8001) extracts events:
   - JSON-LD Schema.org parsing (preferred)
   - LLM-based fallback extraction
   - Pagination detection and following
3. **Django API** stores events with:
   - Schema.org Place data for venues
   - Automatic embedding generation
   - Site strategy tracking

### 6. Chat Service (FastAPI)
**Location:** `chat_service/app.py` (Port 8002)
- Server-Sent Events (SSE) for streaming
- JWT authentication (shared with Django)
- RAG context injection before LLM
- Real-time event recommendations

## Development Setup

### Prerequisites
- Python 3.11+
- PostgreSQL 15+ with pgvector extension
- Ollama with deepseek-llm:7b model

### Quick Start
```bash
# 1. Create and activate virtual environment
python -m venv .venv --prompt "superschedules"
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
export DJANGO_SECRET_KEY=dev-secret-key
export DB_HOST=localhost
export DB_NAME=superschedules
export DB_USER=your_user
export DB_PASSWORD=your_password

# 4. Run migrations
python manage.py migrate

# 5. Create admin user
python manage.py createsuperuser

# 6. Start Django API (Terminal 1)
python manage.py runserver 8000

# 7. Start FastAPI chat service (Terminal 2)
python scripts/start_chat_service.py

# 8. Generate embeddings (Terminal 3)
python manage.py update_embeddings
```

### Running Tests
```bash
# IMPORTANT: Always activate virtualenv before running tests
# Django tests (uses pgvector-aware test runner)
source .venv/bin/activate && python manage.py test --settings=config.test_settings --buffer

# Target specific modules
source .venv/bin/activate && python manage.py test api --settings=config.test_settings --buffer
source .venv/bin/activate && python manage.py test events --settings=config.test_settings --buffer
source .venv/bin/activate && python manage.py test locations --settings=config.test_settings --buffer

# View logs during tests (for debugging)
source .venv/bin/activate && LOG_LEVEL=INFO python manage.py test --settings=config.test_settings

# FastAPI tests (requires DJANGO_SETTINGS_MODULE env var)
source .venv/bin/activate && DJANGO_SETTINGS_MODULE=config.test_settings pytest chat_service/tests/ -v

# API service tests (requires DJANGO_SETTINGS_MODULE env var)
source .venv/bin/activate && DJANGO_SETTINGS_MODULE=config.test_settings pytest api/tests/ -v
```

## API Endpoints

### Django API (Port 8000)
- **Auth**: `POST /api/v1/token/`, `POST /api/v1/token/refresh/`
- **Events**: `GET|POST /api/v1/events/`, `GET|PUT|DELETE /api/v1/events/{id}`
- **Venues**: `GET /api/v1/venues/`, `POST /api/v1/venues/from-osm/` (Navigator integration)
- **Scraping**: `GET /api/v1/queue/next`, `POST /api/v1/queue/{id}/complete`, `POST /api/v1/queue/bulk-submit-service`
- **Scrape History**: `GET /api/v1/scrape/problematic`, `PATCH /api/v1/scrape/history/{id}/notes`
- **Health**: `GET /api/live`, `GET /api/ready`
- **Admin**: `/admin`

### FastAPI Chat (Port 8002)
- **Chat**: `POST /chat/stream` (SSE streaming with RAG)
- **Health**: `GET /health`
- **Debug**: `GET /_debug/tasks`
- **Docs**: `GET /docs` (Swagger UI)

## Integration with Other Repos

### Navigator → Backend (Venue Sync)
```python
# Navigator syncs POIs to backend with discovered events_url
import requests

response = requests.post(
    f"{BACKEND_URL}/api/v1/venues/from-osm/",
    json={
        "osm_type": "node",
        "osm_id": 123456789,
        "name": "Needham Free Public Library",
        "city": "Needham",
        "state": "MA",
        "website": "https://needhamlibrary.org",
        "events_url": "https://needhamlibrary.org/events",  # Calendar page
        "latitude": 42.2834,
        "longitude": -71.2345,
    },
    headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
    timeout=30
)
# Returns: {"venue_id": 226, "status": "created"}
```

### Collector → Backend (Event Results)
```python
# Collector submits scraped events, venues already exist
Event.create_with_schema_org_data(
    event_data,
    venue=job.venue,  # Venue from ScrapingJob
    source_url=job.url
)
```

### Frontend Chat Integration
Frontend (React SPA) connects to FastAPI port 8002 for streaming chat with RAG context.

## Security & Configuration

### Environment Variables
```bash
# Required
DJANGO_SECRET_KEY=your-secret-key
DB_HOST=localhost
DB_NAME=superschedules
DB_USER=your_user
DB_PASSWORD=your_password

# Optional
COLLECTOR_URL=http://localhost:8001  # Collector service
FRONTEND_URL=http://localhost:5173   # For password reset links
PASSWORD_RESET_TIMEOUT=3600          # 1 hour
```

**NEVER commit secrets to version control.** Use `.env` files locally (gitignored).

## Important Reminders

See `SHARED_GUIDELINES.md` for common guidelines (code style, commits, security, AI assistant rules).

**Backend-specific:**
- **When adding Python dependencies**: Update BOTH `requirements.txt` (local dev) AND `requirements-prod.txt` (Docker/production)
- Follow TDD: Write tests first, then implement

## Production Troubleshooting

### Static Files 404 Issues
**Symptoms**: Admin loads but CSS/JS fail with 404, Django returns "Not Found" for `/static/*` requests

**Root Causes**:
1. **Old Docker image without collectstatic** (most common):
   - Check if staticfiles exist: `docker exec <container> ls -la /app/staticfiles/`
   - Verify docker-compose.yml or user_data.sh uses `:latest` tag, not pinned commit hash
   - If pinned, check that commit has `RUN python manage.py collectstatic --noinput` in Dockerfile

2. **WhiteNoise misconfiguration**:
   - Must use middleware ONLY (in settings.MIDDLEWARE), NOT wrapped in asgi.py
   - Remove any `WhiteNoise(django_asgi_app, ...)` wrapper from asgi.py
   - WhiteNoiseMiddleware should be second in MIDDLEWARE list (after SecurityMiddleware)

3. **Multi-stage Docker build missing files**:
   - Ensure staticfiles directory is in final stage, not just builder stage
   - Check COPY commands copy staticfiles to runtime image

**Debug Commands**:
```bash
# Check if staticfiles exist in running container
docker exec <container> ls -la /app/staticfiles/

# Check what image tag is running
docker inspect <container> | grep "Image"

# Test static file locally with same image
docker run --rm -p 8888:8000 <image> &
curl -I http://localhost:8888/static/admin/css/base.css
```

### ALB Health Check Failures

**405 Method Not Allowed**:
- ALB uses HEAD requests, Django Ninja's `@router.get()` only supports GET
- Fix: Use `@router.api_operation(["GET", "HEAD"], "/live", auth=None)` instead
- Affects: `/api/live`, `/api/ready` endpoints

**400 Bad Request**:
- `ALLOWED_HOSTS` doesn't include ALB's IP addresses
- Quick fix: `ALLOWED_HOSTS = ['*']` in production settings
- Better: Add ALB DNS name to ALLOWED_HOSTS

### Health Dashboard Issues

**Collector/Navigator showing red**:
- Environment variables missing: `COLLECTOR_URL` and `NAVIGATOR_URL`
- Default to localhost which doesn't work in Docker containers
- Fix: Set `COLLECTOR_URL=http://collector:8001` and `NAVIGATOR_URL=http://navigator:8004` using Docker service names
- Update in terraform/prod/templates/user_data.sh.tftpl Django environment section
