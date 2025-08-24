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
        """Set up test databases, falling back to SQLite if pgvector isn't available."""
        from django.conf import settings
        
        try:
            # Try to set up PostgreSQL with pgvector
            return super().setup_databases(**kwargs)
        except Exception as e:
            if "vector" in str(e).lower():
                print("⚠️  pgvector not available in test database")
                print("📝 Falling back to SQLite for testing")
                
                # Override database settings for tests
                original_databases = settings.DATABASES.copy()
                settings.DATABASES = {
                    'default': {
                        'ENGINE': 'django.db.backends.sqlite3',
                        'NAME': ':memory:',
                    }
                }
                
                try:
                    # Set up SQLite instead
                    result = super().setup_databases(**kwargs)
                    return result
                finally:
                    # Don't restore original settings here - let tests run
                    pass
            else:
                raise