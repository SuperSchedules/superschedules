FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run Django with gunicorn + uvicorn workers (supports both Django and FastAPI)
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "--workers", "3"]