from __future__ import annotations

from django.test import TestCase, override_settings


class PgVectorTestRunnerTests(TestCase):
    @override_settings()
    def test_fallback_to_sqlite_on_pgvector_error(self):
        # Defer import to use current settings
        from api.test_runner import PgVectorTestRunner
        from django.test.runner import DiscoverRunner
        from django.conf import settings

        # Make the first call raise a vector-related error, then succeed
        calls = {"n": 0}

        def fake_setup(*args, **kwargs):
            if calls["n"] == 0:
                calls["n"] += 1
                raise Exception("vector extension missing")
            else:
                calls["n"] += 1
                return "ok"

        # Patch the parent class method
        orig = DiscoverRunner.setup_databases
        DiscoverRunner.setup_databases = fake_setup  # type: ignore
        try:
            runner = PgVectorTestRunner()
            result = runner.setup_databases()
            assert result == "ok"
            assert calls["n"] == 2
            # Verify fallback settings applied
            assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
            assert settings.DATABASES["default"]["NAME"] == ":memory:"
        finally:
            DiscoverRunner.setup_databases = orig  # type: ignore

