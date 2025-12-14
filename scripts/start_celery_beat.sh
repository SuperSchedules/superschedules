#!/bin/bash
# Start Celery Beat scheduler for development
#
# Usage: ./scripts/start_celery_beat.sh
#
# Uses database scheduler for periodic task management.
# Run setup_celery_beat management command first to configure tasks.

set -e

cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "Starting Celery Beat scheduler..."
echo "Using database scheduler for periodic tasks"
echo ""

celery -A config beat \
    --loglevel=INFO \
    --scheduler=django_celery_beat.schedulers:DatabaseScheduler
