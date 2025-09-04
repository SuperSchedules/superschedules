"""
Test-specific settings that enable pgvector for testing with graceful fallback.
"""
from .settings import *

# Use the robust pgvector test runner with SQLite fallback
TEST_RUNNER = 'test_runner.PgVectorTestRunner'