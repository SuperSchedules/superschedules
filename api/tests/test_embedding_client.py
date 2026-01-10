"""
Tests for the embedding client.

The embedding client provides a unified interface for generating embeddings,
with support for HTTP mode (embedding microservice) and local mode (fallback).
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import numpy as np

from api.embedding_client import EmbeddingClient, get_embedding_client


class TestEmbeddingClientLocal(unittest.TestCase):
    """Test EmbeddingClient in local mode (no service URL)."""

    def test_init_without_service_url(self):
        """Client initializes without service URL, uses local mode."""
        client = EmbeddingClient(service_url=None, fallback_to_local=True)
        self.assertIsNone(client.service_url)
        self.assertTrue(client.fallback_to_local)

    @patch('api.embedding_client.EmbeddingClient._load_local_model')
    def test_encode_single_text_local(self, mock_load_model):
        """Encode single text using local model."""
        mock_model = Mock()
        mock_model.encode.return_value = np.array([[0.1] * 384])
        mock_load_model.return_value = mock_model

        client = EmbeddingClient(service_url=None)
        client._local_model = mock_model
        client._use_local = True

        result = client.encode("test text")

        self.assertEqual(result.shape, (384,))
        mock_model.encode.assert_called_once()

    @patch('api.embedding_client.EmbeddingClient._load_local_model')
    def test_encode_multiple_texts_local(self, mock_load_model):
        """Encode multiple texts using local model."""
        mock_model = Mock()
        mock_model.encode.return_value = np.array([[0.1] * 384, [0.2] * 384])
        mock_load_model.return_value = mock_model

        client = EmbeddingClient(service_url=None)
        client._local_model = mock_model
        client._use_local = True

        result = client.encode(["text1", "text2"])

        self.assertEqual(result.shape, (2, 384))

    @patch('api.embedding_client.EmbeddingClient._load_local_model')
    def test_embedding_cache(self, mock_load_model):
        """Embeddings are cached and reused."""
        mock_model = Mock()
        mock_model.encode.return_value = np.array([[0.1] * 384])
        mock_load_model.return_value = mock_model

        client = EmbeddingClient(service_url=None)
        client._local_model = mock_model
        client._use_local = True

        # First call - should compute
        result1 = client.encode("test text")

        # Second call - should use cache
        result2 = client.encode("test text")

        # Model should only be called once
        self.assertEqual(mock_model.encode.call_count, 1)
        np.testing.assert_array_equal(result1, result2)

    @patch('api.embedding_client.EmbeddingClient._load_local_model')
    def test_cache_normalization(self, mock_load_model):
        """Cache normalizes queries (lowercase, whitespace)."""
        mock_model = Mock()
        mock_model.encode.return_value = np.array([[0.1] * 384])
        mock_load_model.return_value = mock_model

        client = EmbeddingClient(service_url=None)
        client._local_model = mock_model
        client._use_local = True

        # These should all hit the same cache entry
        client.encode("Test Text")
        client.encode("test text")
        client.encode("  TEST   TEXT  ")

        # Model should only be called once
        self.assertEqual(mock_model.encode.call_count, 1)

    @patch('api.embedding_client.EmbeddingClient._load_local_model')
    def test_cache_bypass(self, mock_load_model):
        """Cache can be bypassed with use_cache=False."""
        mock_model = Mock()
        mock_model.encode.return_value = np.array([[0.1] * 384])
        mock_load_model.return_value = mock_model

        client = EmbeddingClient(service_url=None)
        client._local_model = mock_model
        client._use_local = True

        client.encode("test text", use_cache=False)
        client.encode("test text", use_cache=False)

        # Model should be called twice
        self.assertEqual(mock_model.encode.call_count, 2)


class TestEmbeddingClientHTTP(unittest.TestCase):
    """Test EmbeddingClient in HTTP mode (with service URL)."""

    @patch('httpx.Client')
    def test_encode_via_http(self, mock_client_class):
        """Encode text via HTTP service."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [[0.1] * 384],
            "model": "all-MiniLM-L6-v2",
            "processing_time_ms": 5.0,
        }
        mock_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = EmbeddingClient(service_url="http://localhost:8003")
        result = client.encode("test text")

        self.assertEqual(result.shape, (384,))
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        self.assertEqual(call_args[0][0], "http://localhost:8003/embed")

    @patch('httpx.Client')
    @patch('api.embedding_client.EmbeddingClient._load_local_model')
    def test_fallback_on_connection_error(self, mock_load_model, mock_client_class):
        """Falls back to local model on connection error."""
        import httpx

        mock_client = Mock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_class.return_value = mock_client

        mock_model = Mock()
        mock_model.encode.return_value = np.array([[0.1] * 384])
        mock_load_model.return_value = mock_model

        client = EmbeddingClient(service_url="http://localhost:8003", fallback_to_local=True)
        result = client.encode("test text")

        self.assertEqual(result.shape, (384,))
        self.assertTrue(client._use_local)

    @patch('httpx.Client')
    def test_no_fallback_raises_error(self, mock_client_class):
        """Raises error when fallback disabled and service fails."""
        import httpx

        mock_client = Mock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_class.return_value = mock_client

        client = EmbeddingClient(service_url="http://localhost:8003", fallback_to_local=False)

        with self.assertRaises(httpx.ConnectError):
            client.encode("test text")

    @patch('httpx.Client')
    def test_health_check_success(self, mock_client_class):
        """Health check returns service status."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "model_loaded": True,
            "model_name": "all-MiniLM-L6-v2",
        }
        mock_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = EmbeddingClient(service_url="http://localhost:8003")
        health = client.health_check()

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["mode"], "http")
        self.assertTrue(health["model_loaded"])


class TestGetEmbeddingClient(unittest.TestCase):
    """Test the get_embedding_client singleton function."""

    def tearDown(self):
        """Reset global client after each test."""
        import api.embedding_client as module
        module._embedding_client = None

    def test_returns_singleton(self):
        """get_embedding_client returns the same instance."""
        import api.embedding_client as module
        module._embedding_client = None

        client1 = get_embedding_client()
        client2 = get_embedding_client()

        self.assertIs(client1, client2)

    @patch.object(EmbeddingClient, 'warmup')
    def test_warmup_flag(self, mock_warmup):
        """warmup=True calls warmup on the client."""
        import api.embedding_client as module
        module._embedding_client = None

        get_embedding_client(warmup=True)

        mock_warmup.assert_called_once()


if __name__ == '__main__':
    unittest.main()
