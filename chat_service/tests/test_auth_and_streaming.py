"""
Tests for FastAPI authentication and streaming edge cases.
"""

import json
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from fastapi.testclient import TestClient
from fastapi import HTTPException

from chat_service.app import app, verify_jwt_token, merge_async_generators


class AuthAndStreamingTests(TestCase):
    """Test authentication and streaming functionality."""
    
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="auth-test@example.com", 
            email="auth-test@example.com", 
            password="testpass123"
        )
        refresh = RefreshToken.for_user(self.user)
        self.jwt = str(refresh.access_token)
        self.client = TestClient(app)

    def test_missing_auth_header(self):
        """Test missing Authorization header."""
        payload = {"message": "test", "single_model_mode": True}
        response = self.client.post("/chat/stream", json=payload)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Missing or invalid", response.json()["detail"])

    def test_invalid_auth_format(self):
        """Test invalid Authorization header format."""
        headers = {"Authorization": "InvalidFormat token"}
        payload = {"message": "test", "single_model_mode": True}
        response = self.client.post("/chat/stream", json=payload, headers=headers)
        self.assertEqual(response.status_code, 401)

    def test_malformed_token(self):
        """Test malformed JWT token."""
        headers = {"Authorization": "Bearer invalid.token.here"}
        payload = {"message": "test", "single_model_mode": True}
        response = self.client.post("/chat/stream", json=payload, headers=headers)
        self.assertEqual(response.status_code, 401)

    def test_expired_token(self):
        """Test expired JWT token."""
        from datetime import datetime, timezone
        # Create an expired token by manipulating the payload directly
        refresh = RefreshToken.for_user(self.user)
        access_token = refresh.access_token
        # Set expiration to past (expired)
        access_token.payload['exp'] = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
        
        headers = {"Authorization": f"Bearer {str(access_token)}"}
        payload = {"message": "test", "single_model_mode": True}
        response = self.client.post("/chat/stream", json=payload, headers=headers)
        self.assertEqual(response.status_code, 401)

    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_streaming_error_handling(self, mock_get_llm, mock_get_events):
        """Test error handling in streaming response."""
        mock_get_events.return_value = []
        
        # Mock LLM service that raises an exception
        mock_service = MagicMock()
        mock_service.DEFAULT_MODEL_A = "test-model"
        
        async def failing_generator(*args, **kwargs):
            yield {"message": {"content": "Start"}, "done": False, "token": "Start"}
            raise Exception("LLM service error")
        
        mock_service.generate_streaming_response = failing_generator
        mock_get_llm.return_value = mock_service
        
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "test", "single_model_mode": True}
        
        chunks = []
        with self.client.stream("POST", "/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    chunks.append(line[len("data: "):])
        
        # Should have error chunk and system completion
        self.assertGreater(len(chunks), 0)
        # Check that we get some error handling
        error_found = any('error' in chunk or 'LLM service error' in chunk for chunk in chunks)
        self.assertTrue(error_found or len(chunks) > 1, "Should handle streaming errors gracefully")

    @patch("chat_service.app.get_relevant_events", new_callable=AsyncMock)
    @patch("chat_service.app.get_llm_service")
    def test_empty_rag_results(self, mock_get_llm, mock_get_events):
        """Test handling when RAG returns no events."""
        mock_get_events.return_value = []  # No events found
        
        mock_service = MagicMock()
        mock_service.DEFAULT_MODEL_A = "test-model"
        
        async def mock_generator(*args, **kwargs):
            yield {"message": {"content": "No events"}, "done": False, "token": "No events"}
            yield {"message": {"content": ""}, "done": True, "success": True, "response_time_ms": 100}
        
        mock_service.generate_streaming_response = mock_generator
        mock_get_llm.return_value = mock_service
        
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"message": "events in mars", "single_model_mode": True}
        
        chunks = []
        with self.client.stream("POST", "/chat/stream", json=payload, headers=headers) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    chunks.append(line[len("data: "):])
        
        self.assertGreater(len(chunks), 0)
        # Should complete successfully even with no events
        system_completion = any('"model": "SYSTEM"' in chunk for chunk in chunks)
        self.assertTrue(system_completion)

    def test_async_generator_merging(self):
        """Test the async generator merging utility."""
        async def run_test():
            async def gen1():
                yield "A1"
                yield "A2"
            
            async def gen2():
                yield "B1"
                yield "B2"
            
            results = []
            async for item in merge_async_generators(gen1(), gen2()):
                results.append(item)
            
            # Should get all items from both generators
            self.assertEqual(len(results), 4)
            self.assertIn("A1", results)
            self.assertIn("A2", results)
            self.assertIn("B1", results)
            self.assertIn("B2", results)
        
        import asyncio
        asyncio.run(run_test())

    def test_user_not_found_warning(self):
        """Test handling when JWT is valid but user doesn't exist in DB."""
        # Create token for user, then delete the user
        refresh = RefreshToken.for_user(self.user)
        token = str(refresh.access_token)
        self.user.delete()  # User no longer exists
        
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"message": "test", "single_model_mode": True}
        
        # Should still work (logs warning but doesn't fail)
        with patch("chat_service.app.get_relevant_events", new_callable=AsyncMock) as mock_events:
            with patch("chat_service.app.get_llm_service") as mock_llm:
                mock_events.return_value = []
                
                mock_service = MagicMock()
                mock_service.DEFAULT_MODEL_A = "test-model"
                
                async def mock_gen(*args, **kwargs):
                    yield {"message": {"content": "OK"}, "done": True, "success": True, "response_time_ms": 10}
                
                mock_service.generate_streaming_response = mock_gen
                mock_llm.return_value = mock_service
                
                response = self.client.post("/chat/stream", json=payload, headers=headers)
                self.assertEqual(response.status_code, 200)

    @override_settings(JWT_EXPECTED_AUDIENCE='expected-audience')
    def test_jwt_audience_validation_success(self):
        """Test JWT audience validation when token has correct audience."""
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create token with correct audience
        refresh = RefreshToken.for_user(self.user)
        access_token = refresh.access_token
        access_token.payload['aud'] = 'expected-audience'
        
        headers = {"Authorization": f"Bearer {str(access_token)}"}
        payload = {"message": "test", "single_model_mode": True}
        
        with patch("chat_service.app.get_relevant_events", new_callable=AsyncMock) as mock_events:
            with patch("chat_service.app.get_llm_service") as mock_llm:
                mock_events.return_value = []
                mock_service = MagicMock()
                mock_service.DEFAULT_MODEL_A = "test-model"
                
                async def mock_gen(*args, **kwargs):
                    yield {"message": {"content": "OK"}, "done": True, "success": True, "response_time_ms": 10}
                
                mock_service.generate_streaming_response = mock_gen
                mock_llm.return_value = mock_service
                
                response = self.client.post("/chat/stream", json=payload, headers=headers)
                self.assertEqual(response.status_code, 200)

    @override_settings(JWT_EXPECTED_AUDIENCE='expected-audience')
    def test_jwt_audience_validation_failure(self):
        """Test JWT audience validation when token has wrong audience."""
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create token with wrong audience
        refresh = RefreshToken.for_user(self.user)
        access_token = refresh.access_token
        access_token.payload['aud'] = 'wrong-audience'
        
        headers = {"Authorization": f"Bearer {str(access_token)}"}
        payload = {"message": "test", "single_model_mode": True}
        response = self.client.post("/chat/stream", json=payload, headers=headers)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid token audience", response.json()["detail"])

    @override_settings(JWT_EXPECTED_AUDIENCE='expected-audience')
    def test_jwt_audience_validation_missing(self):
        """Test JWT audience validation when token has no audience."""
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create token without audience claim
        refresh = RefreshToken.for_user(self.user)
        access_token = refresh.access_token
        # Remove audience if it exists
        access_token.payload.pop('aud', None)
        
        headers = {"Authorization": f"Bearer {str(access_token)}"}
        payload = {"message": "test", "single_model_mode": True}
        response = self.client.post("/chat/stream", json=payload, headers=headers)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid token audience", response.json()["detail"])

    @override_settings(JWT_EXPECTED_AUDIENCE='expected-audience')
    def test_jwt_audience_validation_list_success(self):
        """Test JWT audience validation when token has audience as list."""
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create token with audience as list containing correct value
        refresh = RefreshToken.for_user(self.user)
        access_token = refresh.access_token
        access_token.payload['aud'] = ['other-audience', 'expected-audience']
        
        headers = {"Authorization": f"Bearer {str(access_token)}"}
        payload = {"message": "test", "single_model_mode": True}
        
        with patch("chat_service.app.get_relevant_events", new_callable=AsyncMock) as mock_events:
            with patch("chat_service.app.get_llm_service") as mock_llm:
                mock_events.return_value = []
                mock_service = MagicMock()
                mock_service.DEFAULT_MODEL_A = "test-model"
                
                async def mock_gen(*args, **kwargs):
                    yield {"message": {"content": "OK"}, "done": True, "success": True, "response_time_ms": 10}
                
                mock_service.generate_streaming_response = mock_gen
                mock_llm.return_value = mock_service
                
                response = self.client.post("/chat/stream", json=payload, headers=headers)
                self.assertEqual(response.status_code, 200)

    @override_settings(JWT_EXPECTED_ISSUER='expected-issuer')
    def test_jwt_issuer_validation_success(self):
        """Test JWT issuer validation when token has correct issuer."""
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create token with correct issuer
        refresh = RefreshToken.for_user(self.user)
        access_token = refresh.access_token
        access_token.payload['iss'] = 'expected-issuer'
        
        headers = {"Authorization": f"Bearer {str(access_token)}"}
        payload = {"message": "test", "single_model_mode": True}
        
        with patch("chat_service.app.get_relevant_events", new_callable=AsyncMock) as mock_events:
            with patch("chat_service.app.get_llm_service") as mock_llm:
                mock_events.return_value = []
                mock_service = MagicMock()
                mock_service.DEFAULT_MODEL_A = "test-model"
                
                async def mock_gen(*args, **kwargs):
                    yield {"message": {"content": "OK"}, "done": True, "success": True, "response_time_ms": 10}
                
                mock_service.generate_streaming_response = mock_gen
                mock_llm.return_value = mock_service
                
                response = self.client.post("/chat/stream", json=payload, headers=headers)
                self.assertEqual(response.status_code, 200)

    @override_settings(JWT_EXPECTED_ISSUER='expected-issuer')
    def test_jwt_issuer_validation_failure(self):
        """Test JWT issuer validation when token has wrong issuer."""
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create token with wrong issuer
        refresh = RefreshToken.for_user(self.user)
        access_token = refresh.access_token
        access_token.payload['iss'] = 'wrong-issuer'
        
        headers = {"Authorization": f"Bearer {str(access_token)}"}
        payload = {"message": "test", "single_model_mode": True}
        response = self.client.post("/chat/stream", json=payload, headers=headers)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid token issuer", response.json()["detail"])