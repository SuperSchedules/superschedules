# Multi-stage build for smaller final image
FROM python:3.12-slim AS builder

# Build arguments for version info
ARG BUILD_TIME
ARG GIT_COMMIT

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies to /install so we can copy them cleanly
COPY requirements-prod.txt .
RUN pip install --no-cache-dir --prefix=/install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir --prefix=/install -r requirements-prod.txt

# Production stage
FROM python:3.12-slim

# Build arguments for version info (need to redeclare in this stage)
ARG BUILD_TIME
ARG GIT_COMMIT

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for running the application
RUN useradd --create-home --shell /bin/bash appuser

# Copy installed packages from builder to system Python
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=appuser:appuser . .

# Generate build_info.py with version metadata
RUN echo "# Auto-generated build information" > /app/build_info.py && \
    echo "BUILD_TIME = '${BUILD_TIME:-unknown}'" >> /app/build_info.py && \
    echo "GIT_COMMIT = '${GIT_COMMIT:-unknown}'" >> /app/build_info.py && \
    chown appuser:appuser /app/build_info.py

# Collect static files into the image (run as root, then fix ownership)
ENV DJANGO_SECRET_KEY=build-time-secret \
    DB_HOST=localhost \
    DB_NAME=placeholder \
    DB_USER=placeholder \
    DB_PASSWORD=placeholder
RUN python manage.py collectstatic --noinput && \
    chown -R appuser:appuser /app/staticfiles

# Switch to non-root user
USER appuser

# Run Django with gunicorn + uvicorn workers (supports both Django and FastAPI)
CMD ["gunicorn", "config.asgi:application", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-b", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-", \
     "--access-logformat", "%(t)s %(h)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\""]
