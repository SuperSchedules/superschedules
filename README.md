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

## Contributing
See `docs/AGENTS.md` for coding conventions, testing, and PR guidelines.
