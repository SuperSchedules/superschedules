# Streaming Chat Setup

This project now includes a FastAPI streaming chat service alongside the main Django application.

## Architecture

```
Frontend (Port 5173)
    ↓
├── Django API (Port 8000)     ← Auth, CRUD, Events
│   └── /api/v1/events/
│   └── /api/v1/auth/
├── Collector (Port 8001)      ← Event scraping
│   └── /scrape/
└── FastAPI Chat (Port 8002)   ← Streaming Chat
    └── /chat/stream
    └── /health
```

## Development Workflow

### 1. Start Django (Terminal 1)
```bash
cd /Users/gregk/superschedules
source schedules_dev/bin/activate
python manage.py runserver 8000
```

### 2. Start FastAPI Chat Service (Terminal 2)
```bash
cd /Users/gregk/superschedules
source schedules_dev/bin/activate
python start_chat_service.py
```

### 3. Start Frontend (Terminal 3)
```bash
cd /Users/gregk/superschedules_frontend
npm run dev
```

## API Endpoints

### FastAPI Chat Service (Port 8001)

**POST /chat/stream**
- Streams dual LLM responses for A/B testing
- Returns Server-Sent Events (SSE)
- Requires Bearer token authentication

**GET /health**
- Health check for the chat service

**GET /docs**
- Interactive API documentation

## How Streaming Works

1. **Frontend** sends message to `/chat/stream`
2. **FastAPI** creates two async tasks for model A and B
3. **Tokens stream** back as Server-Sent Events:
   ```json
   data: {"model": "A", "token": "Hello", "done": false}
   data: {"model": "B", "token": "Hi", "done": false}
   data: {"model": "A", "token": " there!", "done": true, "response_time_ms": 1200}
   ```
4. **Frontend** renders tokens in real-time

## Authentication

The FastAPI service uses the same JWT tokens as Django:
- Tokens are verified using `ninja_jwt.tokens.UntypedToken`
- Django models are accessible via `django.setup()`
- No separate user management needed

## Next Steps

1. **Integrate with your LLM service** - Modify `stream_model_response()` to use actual streaming
2. **Add real RAG** - Implement vector search in `get_relevant_events()`
3. **Error handling** - Add retry logic and better error messages
4. **Rate limiting** - Add rate limiting for chat endpoints
5. **Monitoring** - Add logging and metrics

## Troubleshooting

**Port conflicts**: Make sure ports 8000, 8001, and 5173 are available
**CORS issues**: Frontend origins are configured in `fastapi_app.py`
**Auth issues**: Check that JWT tokens are valid and not expired
**Django setup**: Ensure `DJANGO_SETTINGS_MODULE` is correctly set