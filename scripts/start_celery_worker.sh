#!/bin/bash
# Start Celery worker for development
#
# Usage: ./scripts/start_celery_worker.sh
#
# Processes all queues with conservative concurrency for database-backed broker.

set -e

cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "Starting Celery worker..."
echo "Processing queues: default, embeddings, geocoding, scraping"
echo ""

celery -A config worker \
    --loglevel=INFO \
    --concurrency=2 \
    --queues=default,embeddings,geocoding,scraping
