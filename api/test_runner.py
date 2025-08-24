"""
Custom Django test runner that handles pgvector gracefully.
"""
import os
from django.test.runner import DiscoverRunner


class PgVectorTestRunner(DiscoverRunner):
    """
    Custom test runner that either sets up pgvector or uses SQLite for tests.
    """
    
    def setup_databases(self, **kwargs):
        """Set up test databases, with graceful fallback to SQLite when Postgres/pgvector is unavailable."""
        from django.conf import settings
        
        try:
            # Try to set up PostgreSQL with pgvector
            return super().setup_databases(**kwargs)
        except Exception as e:
            msg = str(e).lower()
            should_fallback = any(
                key in msg for key in [
                    "vector",  # pgvector extension issues
                    "could not connect",
                    "connection refused",
                    "server on socket",
                    "operationalerror",
                ]
            )
            if should_fallback:
                print("⚠️  Postgres/pgvector not available for tests")
                print("📝 Falling back to in-memory SQLite for testing")
                
                # Override database settings for tests
                settings.DATABASES = {
                    'default': {
                        'ENGINE': 'django.db.backends.sqlite3',
                        'NAME': ':memory:',
                    }
                }
                
                # Set up SQLite instead
                return super().setup_databases(**kwargs)
            else:
                raise
