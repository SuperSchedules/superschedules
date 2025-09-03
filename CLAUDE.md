# Superschedules

AI-powered local events discovery. Users ask: *"Activities for 3-5 year olds in Newton, next 3 hours"* and get intelligent recommendations via RAG.

## Architecture
```
React Frontend → Django API (auth/data) → FastAPI Chat Service → RAG → Event Recommendations
```

## Development Guidelines

### Test-Driven Development (TDD)
**Always start with tests when adding new features:**
1. Write failing tests first
2. Implement minimal code to make tests pass
3. Refactor while keeping tests green
4. Run test suite: `python manage.py test`

### Function Design
Write functions using easily testable subfunctions:
- Break complex logic into small, pure functions
- Each function should have a single responsibility
- Minimize dependencies and side effects
- Use dependency injection for external services
- keep returns human readable where possible, and try to make debugging easier by not having complicated logic on a return statement.
- avoid comments that are just repeating the function name.
- 
Example:
```python
# Good: Testable subfunctions
def parse_event_date(date_string: str) -> datetime:
    """Pure function, easy to test"""
    return datetime.fromisoformat(date_string)

def validate_event_data(event_data: dict) -> bool:
    """Pure validation logic"""
    return all(key in event_data for key in ['title', 'date', 'location'])

def create_event(event_data: dict, api_client: ApiClient) -> Event:
    """Main function using testable subfunctions"""
    if not validate_event_data(event_data):
        raise ValueError("Invalid event data")
    
    parsed_date = parse_event_date(event_data['date'])
    return api_client.post_event({**event_data, 'date': parsed_date})
```

## Tech Stack
- **Backend**: Django 5.0 with Django Ninja API framework
- **Chat Service**: FastAPI with Server-Sent Events (SSE) streaming  
- **Database**: PostgreSQL with pgvector extension
- **LLM**: Ollama
- **RAG**: sentence-transformers (all-MiniLM-L6-v2) + vector similarity
- **Testing**: django tests with manage.py test or pytest for fast api
- **Auth**: JWT tokens via ninja-jwt

## Services
- Django API: Port 8000 (auth, events, admin)
- FastAPI Chat: Port 8002 (streaming chat with RAG)

## Key Components

### Django Backend
- Event storage and API endpoints with Django Ninja
- JWT authentication and service tokens
- PostgreSQL with pgvector for vector operations
- Grappelli admin interface

### FastAPI Chat Service
- Real-time streaming chat with SSE
- RAG integration for contextual responses
- CORS enabled for frontend integration

### RAG System (Implemented)
- Semantic search using sentence transformers
- Vector embeddings stored in PostgreSQL with pgvector
- Cosine similarity for event matching
- Real-time context retrieval for chat responses
- Location extraction and temporal filtering

### Event Collection
- JSON-LD extraction → LLM fallback → Enhanced scraping
- Site-specific strategies and pagination detection
- Scraping job tracking and batch processing

## Key Files
- `events/models.py` - Event, Source, SiteStrategy models with vector fields
- `api/llm_service.py` - Ollama integration with A/B testing
- `api/rag_service.py` - Semantic search and embedding management
- `chat_service/app.py` - FastAPI streaming chat interface
- `api/tests/` & `chat_service/tests/` - Comprehensive test suites

## Testing Commands
- Django tests: `python manage.py test`
- API tests: `pytest api/tests/`
- Chat service tests: `pytest chat_service/tests/`
