"""
Test-specific settings that enable pgvector for testing with graceful fallback.
"""
import os
import warnings
from .settings import *
from copy import deepcopy
from django.conf import settings

# Suppress warnings during tests for clean output
warnings.filterwarnings('ignore', category=RuntimeWarning)

# Use the robust pgvector test runner with SQLite fallback

# Create unique test database name to avoid conflicts in parallel runs
test_db_name = "test_superschedules"
if os.environ.get('TEST_DB_SUFFIX'):
    test_db_name += os.environ.get('TEST_DB_SUFFIX')

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "superschedules",
        "USER": "gregk",          # <- your creds
        "PASSWORD": "",      # <- your creds
        "HOST": "",
        "PORT": "",
        "CONN_MAX_AGE": 0,           # avoid persistent conns in tests
        "TEST": {
            "NAME": test_db_name,  # unique test DB name to avoid conflicts
        },
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

TEST_RUNNER = "test_runner.PgVectorTestRunner"

# Suppress logging during tests for clean output
# Can override with LOG_LEVEL environment variable
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.NullHandler',  # Suppress output during tests
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'CRITICAL',  # Suppress root logger
    },
    'loggers': {
        'api': {
            'handlers': ['console'],
            'level': os.environ.get('LOG_LEVEL', 'CRITICAL'),
            'propagate': False,
        },
        'events': {
            'handlers': ['console'],
            'level': os.environ.get('LOG_LEVEL', 'CRITICAL'),
            'propagate': False,
        },
        'chat_service': {
            'handlers': ['console'],
            'level': os.environ.get('LOG_LEVEL', 'CRITICAL'),
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'CRITICAL',  # Suppress Django request warnings
            'propagate': False,
        },
    },
}

# Celery test settings - run tasks synchronously
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'

# Disable Turnstile bot protection for tests
TURNSTILE_SECRET_KEY = ""
