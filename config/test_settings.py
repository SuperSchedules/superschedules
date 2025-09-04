"""
Test-specific settings that enable pgvector for testing with graceful fallback.
"""
from .settings import *
from copy import deepcopy
from django.conf import settings

# Use the robust pgvector test runner with SQLite fallback

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
            "NAME": "test_superschedules",  # explicit test DB name
        },
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

TEST_RUNNER = "test_runner.PgOnlyRunner"
