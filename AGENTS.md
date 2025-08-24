# Repository Guidelines

## Project Structure & Module Organization
- `config/`: Django project config (`settings.py`, `urls.py`).
- `events/`: Core app (models, admin, migrations, tests/).
- `api/`: Service layer (e.g., `rag_service.py`, `llm_service.py`, auth/health views, tests).
- `chat_service/`: FastAPI streaming chat (`fastapi_app.py`).
- Top level: `manage.py`, `requirements.txt`, `STREAMING_SETUP.md`, utility scripts.

## Build, Test, and Development Commands
- Create env: `python -m venv .venv && source .venv/bin/activate`
- Install deps: `pip install -r requirements.txt`
- Migrate DB: `python manage.py migrate`
- Run Django API: `python manage.py runserver 8000`
- Run chat service: `python start_chat_service.py` (or `uvicorn chat_service.fastapi_app:app --reload --port 8002`)
- Run tests: `python manage.py test` (uses custom pgvector-aware test runner)

## Coding Style & Naming Conventions
- Python 3, PEP 8, 4‑space indents; prefer type hints and docstrings for public functions.
- Django: app modules lowercase (`events`, `api`); models `CamelCase`, functions/variables `snake_case`.
- Tests: name files `tests.py` or place under `tests/`; test classes extend `django.test.TestCase`.
- Keep views thin; place LLM/RAG logic in `api/` services; avoid circular imports.

## Testing Guidelines
- Framework: Django test runner (configured as `api.test_runner.PgVectorTestRunner`). Falls back to SQLite if pgvector isn’t available.
- Scope: add unit tests for new models, views, and service functions (e.g., prompt formatting, RAG search). Include regression tests for bugs.
- Running: `python manage.py test` or target a module (e.g., `python manage.py test api`).
- Data: use factories/fixtures already in use (e.g., `model_bakery`, `django_dynamic_fixture`).

## Commit & Pull Request Guidelines
- Commits: use clear, imperative subjects; prefer Conventional Commits prefixes (`feat:`, `fix:`, `refactor:`, `chore:`). Keep ≤72 chars; include rationale in body when useful.
- PRs: concise title/description, link issues, list changes, include test plan/output. Add screenshots for UI-facing changes. Reference affected endpoints when applicable.

## Security & Configuration Tips
- Copy `.env.example` to local env and set secrets (e.g., `DJANGO_SECRET_KEY`, DB settings). Do not commit secrets.
- DB: PostgreSQL with `pgvector` in prod; see `install_postgres_macos.sh`. Tests auto-handle missing pgvector.
- CORS/ports: default local dev uses 8000 (Django) and 8002 (chat); see `STREAMING_SETUP.md` for architecture and workflow.

