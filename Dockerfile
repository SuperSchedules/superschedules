# Multi-stage build for smaller final image
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements-prod.txt .
RUN pip install --no-cache-dir --user torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir --user -r requirements-prod.txt

# Production stage
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY . .

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Collect static files into the image
# Set minimal environment variables needed for collectstatic
ENV DJANGO_SECRET_KEY=build-time-secret \
    DB_HOST=localhost \
    DB_NAME=placeholder \
    DB_USER=placeholder \
    DB_PASSWORD=placeholder
RUN python manage.py collectstatic --noinput

# Run Django with gunicorn + uvicorn workers (supports both Django and FastAPI)
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "--workers", "3"]