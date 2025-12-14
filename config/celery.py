"""
Celery configuration for Superschedules.

Uses AWS SQS as message broker for production:
- Reliable, managed service (no maintenance)
- Scales automatically with load
- Integrated with AWS IAM for security
- Better for production than database broker

Results are stored in django-celery-results (database).
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
