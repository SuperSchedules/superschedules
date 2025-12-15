# Multi-stage build for smaller final image
FROM python:3.12-slim AS builder

# Build arguments for version info
ARG BUILD_TIME
ARG GIT_COMMIT

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install build dependencies (including libcurl for pycurl/SQS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libcurl4-openssl-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements-prod.txt .
RUN pip install --no-cache-dir --user torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir --user -r requirements-prod.txt

# Production stage
FROM python:3.12-slim

# Build arguments for version info (need to redeclare in this stage)
ARG BUILD_TIME
ARG GIT_COMMIT

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install only runtime dependencies (including libcurl for pycurl/SQS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libcurl4 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY . .

# Generate build_info.py with version metadata
RUN echo "# Auto-generated build information" > /app/build_info.py && \
    echo "BUILD_TIME = '${BUILD_TIME:-unknown}'" >> /app/build_info.py && \
    echo "GIT_COMMIT = '${GIT_COMMIT:-unknown}'" >> /app/build_info.py

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
CMD ["gunicorn", "config.asgi:application", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-b", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-", \
     "--access-logformat", "%(t)s %(h)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\""]
