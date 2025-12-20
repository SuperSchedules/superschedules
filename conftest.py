"""
pytest configuration for superschedules project.

This file initializes Django before any pytest tests are collected or run.
It ensures that Django models, apps, and settings are properly loaded
before test files import Django-dependent modules like model_bakery.
"""

import os
import django
from django.conf import settings


def pytest_configure():
    """Initialize Django with test settings before pytest collects tests."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
    django.setup()
