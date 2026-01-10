"""
Tests for the embedding microservice.

Run with: pytest embedding_service/tests/ -v
"""

import pytest
from unittest.mock import Mock, patch
import numpy as np


@pytest.fixture
def mock_model():
    """Mock sentence transformer model."""
    model = Mock()
    model.encode.return_value = np.array([[0.1] * 384])
    return model


@pytest.fixture
def client(mock_model):
    """Create test client with mocked model."""
    with patch('embedding_service.app.SentenceTransformer', return_value=mock_model):
        # Import after patching
        from embedding_service.app import app, startup_event
        import asyncio

        # Manually trigger startup to load model
        asyncio.get_event_loop().run_until_complete(startup_event())

        from fastapi.testclient import TestClient
        yield TestClient(app)


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_ok(self, client):
        """Health endpoint returns ok when model loaded."""
        response = client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "ok"
        assert data["model_loaded"] is True
        assert data["model_name"] == "all-MiniLM-L6-v2"


class TestEmbedEndpoint:
    """Tests for /embed endpoint."""

    def test_embed_single_text(self, client, mock_model):
        """Embed a single text."""
        response = client.post("/embed", json={"texts": ["hello world"]})
        assert response.status_code == 200

        data = response.json()
        assert len(data["embeddings"]) == 1
        assert len(data["embeddings"][0]) == 384
        assert data["model"] == "all-MiniLM-L6-v2"
        assert "processing_time_ms" in data

    def test_embed_multiple_texts(self, client, mock_model):
        """Embed multiple texts."""
        mock_model.encode.return_value = np.array([[0.1] * 384, [0.2] * 384, [0.3] * 384])

        response = client.post("/embed", json={"texts": ["text1", "text2", "text3"]})
        assert response.status_code == 200

        data = response.json()
        assert len(data["embeddings"]) == 3
        for emb in data["embeddings"]:
            assert len(emb) == 384

    def test_embed_empty_list_fails(self, client):
        """Empty text list returns validation error."""
        response = client.post("/embed", json={"texts": []})
        assert response.status_code == 422  # Validation error

    def test_embed_too_many_texts_fails(self, client):
        """More than 100 texts returns validation error."""
        texts = [f"text{i}" for i in range(101)]
        response = client.post("/embed", json={"texts": texts})
        assert response.status_code == 422  # Validation error


class TestEmbedSingleEndpoint:
    """Tests for /embed/single endpoint."""

    def test_embed_single_convenience(self, client, mock_model):
        """Embed single text via convenience endpoint."""
        response = client.post("/embed/single?text=hello")
        assert response.status_code == 200

        data = response.json()
        assert len(data["embedding"]) == 384
        assert data["model"] == "all-MiniLM-L6-v2"


class TestModelNotLoaded:
    """Tests for when model is not yet loaded."""

    def test_embed_before_startup_fails(self):
        """Embed fails if model not loaded (startup not complete)."""
        # Create fresh app without triggering startup
        with patch('embedding_service.app._model', None):
            from embedding_service.app import app
            from fastapi.testclient import TestClient

            # Need to reimport to reset global
            import embedding_service.app as module
            original_model = module._model
            module._model = None

            try:
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post("/embed", json={"texts": ["test"]})
                assert response.status_code == 503
            finally:
                module._model = original_model
