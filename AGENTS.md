# Superschedules - Repository Guidelines

## Overview
This is the **main backend repository** for Superschedules, an AI-powered local events discovery platform. This repo contains the Django API, FastAPI chat service, and RAG system.

## Multi-Repository System
- **superschedules_frontend**: React SPA with chat interface
- **superschedules** (THIS REPO): Django API + FastAPI chat service
- **superschedules_collector**: Event page parsing and extraction
- **superschedules_navigator**: Site scanning for event discovery
- **superschedules_IAC**: Terraform infrastructure code

## Project Structure & Module Organization

```
superschedules/
├── config/              # Django project config (settings.py, urls.py)
├── events/              # Core Django app
│   ├── models.py       # Event, Source, SiteStrategy, ScrapingJob
│   ├── place_models.py # Schema.org Place model for venues
│   ├── admin.py        # Grappelli admin with collector integration
│   ├── tests/          # Event model and API tests
│   └── management/
│       └── commands/
│           └── update_embeddings.py  # RAG embedding management
├── api/                 # Service layer
│   ├── views.py        # Django Ninja API endpoints
│   ├── llm_service.py  # Ollama/Deepseek integration
│   ├── rag_service.py  # Semantic search with sentence-transformers
│   ├── auth.py         # JWT + service token authentication
│   ├── health.py       # Health/readiness endpoints
│   └── tests/          # API, RAG, LLM tests
├── chat_service/        # FastAPI streaming chat
│   ├── app.py          # SSE streaming endpoints with RAG context
│   ├── debug_routes.py # Task debugging endpoints
│   └── tests/          # FastAPI integration tests
├── scripts/             # Utility scripts (start_chat_service.py)
├── requirements.txt     # Python dependencies
└── manage.py           # Django management
```

## Tech Stack
- **Backend**: Django 5.0 with Django Ninja API
- **Chat**: FastAPI with Server-Sent Events (SSE) streaming
- **Database**: PostgreSQL 15+ with pgvector extension
- **LLM**: Ollama running Deepseek (local development)
- **RAG**: sentence-transformers (all-MiniLM-L6-v2) + pgvector cosine similarity
- **Auth**: JWT tokens via ninja-jwt
- **Testing**: Django test runner (pgvector-aware) + pytest for FastAPI
- **Admin**: Grappelli interface

## Build, Test, and Development Commands

### Environment Setup
```bash
# Create and activate virtual environment
python -m venv .venv --prompt "superschedules"
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
export DJANGO_SECRET_KEY=dev-secret-key
export DB_HOST=localhost
export DB_NAME=superschedules
export DB_USER=your_user
export DB_PASSWORD=your_password

# Run migrations
python manage.py migrate

# Create admin user
python manage.py createsuperuser
```

### Running Services
```bash
# Django API (Terminal 1) - Port 8000
python manage.py runserver 8000

# FastAPI Chat Service (Terminal 2) - Port 8002
python scripts/start_chat_service.py
# OR: uvicorn chat_service.app:app --reload --port 8002

# Generate RAG embeddings (Terminal 3)
python manage.py update_embeddings
```

### Running Tests
```bash
# Django tests (uses custom pgvector-aware test runner)
python manage.py test --settings=config.test_settings

# Target specific modules
python manage.py test api
python manage.py test events
python manage.py test chat_service

# FastAPI tests
pytest chat_service/tests/

# API service tests
pytest api/tests/
```

## Coding Style & Naming Conventions
- **Language**: Python 3.11+, PEP 8, 4-space indentation
- **Line length**: 120 characters maximum (prefer minimal line wrapping for better readability)
- **Type hints**: Required for all public functions with docstrings
- **Django conventions**:
  - App modules: lowercase (`events`, `api`)
  - Models: `CamelCase` (e.g., `Event`, `SiteStrategy`)
  - Functions/variables: `snake_case`
- **Architecture**: Keep views thin; place LLM/RAG logic in `api/` services
- **Imports**: Avoid circular imports; use absolute imports

## Testing Guidelines

### Test-Driven Development (TDD)
**IMPORTANT: Always write tests before implementing features:**
1. Write failing tests first
2. Implement minimal code to make tests pass
3. Refactor while keeping tests green
4. Run test suite to verify

### Testing Framework
- **Django**: Custom test runner `api.test_runner.PgVectorTestRunner`
  - Uses PostgreSQL with pgvector in development
  - Falls back to SQLite if pgvector isn't available
- **FastAPI**: pytest with async support

### Test Scope
- Unit tests for all new models, views, and service functions
- Test prompt formatting, RAG search, LLM integration
- Include regression tests for bug fixes
- Mock external services (Collector API, Ollama)

### Test Data
Use factories/fixtures already in use:
- `model_bakery`
- `django_dynamic_fixture`

### Function Design for Testability
Write functions using easily testable subfunctions:
- Break complex logic into small, pure functions (single responsibility)
- Minimize dependencies and side effects
- Use dependency injection for external services (LLM, RAG, Collector API)
- Keep returns human-readable for easier debugging
- Avoid complicated logic in return statements

**Example:**
```python
# Good: Testable subfunctions
def parse_event_date(date_string: str) -> datetime:
    """Pure function, easy to test"""
    return datetime.fromisoformat(date_string)

def validate_event_data(event_data: dict) -> bool:
    """Pure validation logic"""
    required_fields = ['title', 'date', 'location']
    return all(key in event_data for key in required_fields)

def create_event(event_data: dict, api_client: ApiClient) -> Event:
    """Main function using testable subfunctions"""
    if not validate_event_data(event_data):
        raise ValueError("Invalid event data")

    parsed_date = parse_event_date(event_data['date'])
    return api_client.post_event({**event_data, 'date': parsed_date})
```

## Commit & Pull Request Guidelines

### Commit Format
Use Conventional Commits format:
- `feat:` New features
- `fix:` Bug fixes
- `refactor:` Code restructuring
- `test:` Test additions/modifications
- `chore:` Build/tooling changes
- `docs:` Documentation updates

**Requirements:**
- Clear, imperative subjects (≤72 characters)
- Include rationale in commit body when useful
- Reference issue numbers when applicable

**Examples:**
```
feat: add Schema.org Place support for event locations
fix: handle missing embeddings in RAG search
test: add unit tests for location parsing helpers
refactor: extract event creation logic into testable functions
```

### Pull Request Guidelines
- **Title**: Concise summary following commit conventions
- **Description**: Link related issues, list changes
- **Test plan**: Include test output or screenshots
- **UI changes**: Add before/after screenshots
- **API changes**: Reference affected endpoints

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

### Security Best Practices
- **NEVER commit secrets to version control**
- Use `.env` files locally (gitignored)
- Copy `.env.example` as template
- Use environment variables for all credentials

### Database
- **Production**: PostgreSQL 15+ with `pgvector` extension
- **Testing**: PostgreSQL (preferred) or SQLite fallback
- Tests auto-handle missing pgvector

### CORS & Ports
- **Django API**: Port 8000 (auth, events, admin)
- **FastAPI Chat**: Port 8002 (streaming chat with RAG)
- **Collector**: Port 8001 (event extraction)
- **Frontend**: Port 5173/5174 (default CORS origins)

See `STREAMING_SETUP.md` for detailed architecture and workflow.

## Key Integration Points

### Collector API Integration
```python
# From api/views.py or events/admin.py
import requests

response = requests.post(
    f"{COLLECTOR_URL}/extract",
    json={
        "url": source.base_url,
        "extraction_hints": {
            "content_selectors": source.site_strategy.best_selectors,
            "additional_hints": {}
        }
    },
    timeout=180
)

# Creates events with Schema.org data
Event.create_with_schema_org_data(event_data, source)
```

### RAG System Usage
```python
from api.rag_service import get_rag_service

rag = get_rag_service()
results = rag.search_events("activities for kids in Newton", limit=5)
```

## Common Development Tasks

### Update Event Embeddings
```bash
# Update all events missing embeddings
python manage.py update_embeddings

# Force update specific events
python manage.py update_embeddings --event-ids 1 2 3

# Force update all events
python manage.py update_embeddings --force
```

### Admin Actions
- Process sources via collector: Select sources in admin → Actions → "Process selected sources via collector API"
- View event details with rich location data
- Track scraping job status and results

## Troubleshooting

### Port Conflicts
Free up ports 8000, 8001, 8002 if services won't start

### CORS Issues
Check CORS origins in `config/settings.py` and `chat_service/app.py`

### Database Connection
Verify PostgreSQL is running and credentials are correct

### LLM Issues
- Ensure Ollama is running: `ollama list`
- Install required models: `ollama pull deepseek-llm:7b llama3.2:3b`

### Embedding Issues
Run `python manage.py update_embeddings` to regenerate missing embeddings

## For AI Assistants (Codex, Claude, etc.)
- **NEVER create files unless absolutely necessary** - Always prefer editing existing files
- **NEVER proactively create documentation files** (*.md, README) unless explicitly requested
- Follow TDD: Write tests first, then implement
- Keep functions small and testable with dependency injection
- Use type hints and clear docstrings
- Reference file locations with `file_path:line_number` format
- **Line length: 120 characters maximum** - Minimize line wrapping for better readability
- Avoid emojis unless explicitly requested by user
