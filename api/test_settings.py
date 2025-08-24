"""
Test-specific settings that enable pgvector for testing.
"""
from config.settings import *

# Use a custom test runner that sets up pgvector
class PgVectorTestRunner:
    """Custom test database creation that ensures pgvector extension."""
    
    def setup_databases(self, **kwargs):
        from django.db import connection
        from django.test.utils import setup_test_environment, teardown_test_environment
        from django.test.runner import setup_databases
        
        # Enable pgvector in test database
        old_config = setup_databases(
            verbosity=kwargs.get('verbosity', 1),
            interactive=kwargs.get('interactive', True),
            keepdb=kwargs.get('keepdb', False),
            debug_sql=kwargs.get('debug_sql', False),
            parallel=kwargs.get('parallel', 0),
            aliases=kwargs.get('aliases', None),
        )
        
        # Enable the pgvector extension
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        
        return old_config

# Use default settings but ensure we can create the extension
TEST_RUNNER = 'django.test.runner.DiscoverRunner'