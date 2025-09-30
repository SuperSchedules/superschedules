from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import patch, AsyncMock

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from fastapi.testclient import TestClient

from chat_service.app import app


class FastAPIDualStreamTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="fa2-user@example.com", email="fa2-user@example.com", password="pass1234"
        )
        refresh = RefreshToken.for_user(self.user)
        self.jwt = str(refresh.access_token)
        self.client = TestClient(app)

