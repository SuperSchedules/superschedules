from __future__ import annotations

from django.test import TestCase
from django.core.management import call_command
from unittest.mock import patch, MagicMock


class SetupTestDbCommandTests(TestCase):
    def test_handles_errors_gracefully(self):
        # Patch connection.cursor to raise an error
        with patch("api.management.commands.setup_test_db.connection") as mock_conn:
            ctx = MagicMock()
            ctx.cursor.side_effect = Exception("no extension")
            mock_conn.__enter__ = lambda s: s
            mock_conn.__exit__ = lambda *a, **k: None
            mock_conn.cursor.side_effect = Exception("no extension")

            # Should not raise
            call_command("setup_test_db")

