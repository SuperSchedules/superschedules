# Superschedules

## Overview
Superschedules is a Django-based backend for event discovery and management, with an optional FastAPI chat service for streaming LLM-powered assistance.

## Prerequisites
- Python 3.11+, PostgreSQL 15+ (pgvector required), pip

## Quickstart
- Create/activate env: `python -m venv .venv --prompt "schedules_dev" && source .venv/bin/activate`
- Install deps: `pip install -r requirements.txt`
- Configure env (example):
  - `export DJANGO_SECRET_KEY=dev-secret`
  - `export DB_HOST=localhost DB_PORT=5432`
  - `export DB_NAME=superschedules DB_USER=superschedules DB_PASSWORD=yourpass`
- Migrations: `python manage.py migrate`
- Create admin: `python manage.py createsuperuser`
- Run API: `python manage.py runserver 8000`
- Run Chat: `python scripts/start_chat_service.py` (FastAPI on 8002)

## Docker Development Setup
### Prerequisites
- Docker installed and running
- PostgreSQL running locally with peer authentication OR TCP with credentials
- Ollama running locally (for chat/LLM features)

### Build and Run
```bash
# Build the Docker image
docker build -t superschedules-api .

# Run with local PostgreSQL and Ollama access
docker run -d --network=host \
  -v /var/run/postgresql:/var/run/postgresql \
  -v ~/.cache:/home/gregk/.cache \
  --user 1000:1000 \
  -e DB_USER=gregk \
  -e TRANSFORMERS_CACHE=/home/gregk/.cache \
  superschedules-api
```

### Docker Configuration Notes
- `--network=host`: Allows container to access local PostgreSQL and Ollama
- `-v /var/run/postgresql:/var/run/postgresql`: Mounts PostgreSQL socket for peer auth
- `-v ~/.cache:/home/gregk/.cache`: Provides writable cache for sentence-transformers models
- `--user 1000:1000`: Runs as your user to match PostgreSQL peer authentication
- `-e DB_USER=gregk`: Sets database user (adjust to match your setup)

### Alternative: TCP Database Connection
If using TCP instead of Unix socket:
```bash
docker run -d --network=host \
  -v ~/.cache:/home/gregk/.cache \
  --user 1000:1000 \
  -e DB_HOST=localhost \
  -e DB_USER=your_db_user \
  -e DB_PASSWORD=your_password \
  -e TRANSFORMERS_CACHE=/home/gregk/.cache \
  superschedules-api
```

### Health Check
Test the container status:
```bash
curl http://localhost:8000/health | jq .
```

Expected healthy response:
```json
{
  "status": "healthy",
  "service": "chat_service", 
  "database": "connected",
  "llm": "connected",
  "models": {
    "primary": {"name": "deepseek-llm:7b", "available": true},
    "backup": {"name": "llama3.2:3b", "available": true}
  }
}
```

## Services & Ports
- Django API: `http://localhost:8000`
- Admin: `/admin`
- Chat service (FastAPI): `http://localhost:8002` (see `docs/STREAMING_SETUP.md`)

## API & Auth
- Health: `GET /api/live`, `GET /api/ready`
- JWT: `POST /api/v1/token/`, `POST /api/v1/token/refresh/`

## Running Tests
```bash
source .venv/bin/activate
python manage.py test --settings=config.test_settings
```

Uses custom test runner with pgvector support and SQLite fallback.

## Project Structure
- `config/`: Django project config (`settings.py`, `urls.py`)
- `events/`: Core app (models, admin, migrations, tests/)
- `api/`: Services (RAG/LLM), API views, tests
- `chat_service/`: FastAPI streaming chat (`app.py`)
- `scripts/`: Utility scripts (e.g., start chat, prompt tests)
- `docs/`: Additional documentation (streaming setup, agents)

## Troubleshooting
- Ports busy: free 8000/8002
- CORS: default origins are 5173/5174 in settings
- Env: copy `.env.example` and export required vars; never commit secrets

### Docker Issues
- **Database connection failed**: Check PostgreSQL is running and user/permissions are correct
- **Peer authentication failed**: Ensure `--user 1000:1000` matches your user ID (`id` command)
- **Permission denied /.cache**: Make sure cache directory is mounted and writable
- **LLM timeout/error**: Verify Ollama is running (`ollama list`) and models are available
- **No models available**: Install required models with `ollama pull deepseek-llm:7b llama3.2:3b`
- **Container won't start**: Check logs with `docker logs <container_id>`

## Contributing
See `docs/AGENTS.md` for coding conventions, testing, and PR guidelines.
