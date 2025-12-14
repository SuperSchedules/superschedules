"""
Django project configuration.

Ensures Celery app is loaded when Django starts.
"""

try:
    from .celery import app as celery_app
    __all__ = ('celery_app',)
except ImportError:
    # Celery not installed - web app can still run without it
    celery_app = None
    __all__ = ()
