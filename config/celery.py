"""
Celery configuration for Superschedules.

Uses PostgreSQL database as message broker (django-db backend) since:
- Tasks are idempotent bookkeeping operations
- No need for Redis/RabbitMQ overhead
- Jobs already use database for state management
"""

import os
from celery import Celery

# Set Django settings module before importing anything else
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('superschedules')

# Load config from Django settings with CELERY_ prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks in all INSTALLED_APPS
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for testing Celery configuration."""
    print(f'Request: {self.request!r}')
