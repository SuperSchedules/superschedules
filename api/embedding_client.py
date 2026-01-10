"""
Embedding client - abstracts embedding generation via HTTP or local model.

This client provides a unified interface for getting embeddings, with two modes:
1. HTTP mode (default): Calls the embedding microservice for shared model usage
2. Local mode (fallback): Loads sentence-transformers locally if service unavailable

Usage:
    from api.embedding_client import get_embedding_client

    client = get_embedding_client()
    embeddings = client.encode(["text1", "text2"])  # Returns numpy array
"""

import logging
import os
import time
from typing import List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """
    Client for generating text embeddings.

    Supports HTTP mode (embedding microservice) and local mode (fallback).
    """

    def __init__(
        self,
        service_url: Optional[str] = None,
        fallback_to_local: bool = True,
        timeout: float = 10.0,
    ):
        """
        Initialize the embedding client.

        Args:
            service_url: URL of embedding service (e.g., "http://localhost:8003").
                         If None, uses EMBEDDING_SERVICE_URL env var or falls back to local.
            fallback_to_local: If True, load model locally when service unavailable.
            timeout: HTTP request timeout in seconds.
        """
        self.service_url = service_url or os.environ.get("EMBEDDING_SERVICE_URL")
        self.fallback_to_local = fallback_to_local
        self.timeout = timeout

        # HTTP client (lazy-loaded)
        self._http_client = None

        # Local model (lazy-loaded, only if fallback needed)
        self._local_model = None
        self._use_local = False  # Set to True if service fails and we fall back

        # Cache for repeated queries (shared between modes)
        self._embedding_cache: dict[str, np.ndarray] = {}
        self._cache_max_size: int = 1000

    def _get_http_client(self):
        """Lazy-load HTTP client."""
        if self._http_client is None:
            import httpx
            self._http_client = httpx.Client(timeout=self.timeout)
        return self._http_client

    def _load_local_model(self):
        """Lazy-load local sentence transformer model."""
        if self._local_model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading local sentence transformer model (fallback mode)")
            self._local_model = SentenceTransformer("all-MiniLM-L6-v2")
            # Warmup
            _ = self._local_model.encode(["warmup"], convert_to_numpy=True)
            logger.info("Local model loaded and warmed up")
        return self._local_model

    def _normalize_query(self, query: str) -> str:
        """Normalize query for cache key."""
        import re
        return re.sub(r'\s+', ' ', query.lower().strip())

    def _get_cached(self, text: str) -> Optional[np.ndarray]:
        """Get embedding from cache if available."""
        normalized = self._normalize_query(text)
        return self._embedding_cache.get(normalized)

    def _cache_embedding(self, text: str, embedding: np.ndarray):
        """Cache an embedding."""
        if len(self._embedding_cache) >= self._cache_max_size:
            # Simple eviction: clear half
            keys_to_remove = list(self._embedding_cache.keys())[:self._cache_max_size // 2]
            for key in keys_to_remove:
                del self._embedding_cache[key]
            logger.debug(f"Embedding cache pruned to {len(self._embedding_cache)} entries")

        normalized = self._normalize_query(text)
        self._embedding_cache[normalized] = embedding

    def encode(self, texts: Union[str, List[str]], use_cache: bool = True) -> np.ndarray:
        """
        Generate embeddings for one or more texts.

        Args:
            texts: Single text string or list of texts
            use_cache: Whether to use/update the embedding cache

        Returns:
            numpy array of embeddings, shape (n_texts, 384)
        """
        # Normalize input
        if isinstance(texts, str):
            texts = [texts]
            single_input = True
        else:
            single_input = False

        # Check cache for all texts
        if use_cache:
            cached_results = []
            uncached_texts = []
            uncached_indices = []

            for i, text in enumerate(texts):
                cached = self._get_cached(text)
                if cached is not None:
                    cached_results.append((i, cached))
                else:
                    uncached_texts.append(text)
                    uncached_indices.append(i)

            if not uncached_texts:
                # All cached
                result = np.array([emb for _, emb in sorted(cached_results)])
                return result[0] if single_input else result
        else:
            uncached_texts = texts
            uncached_indices = list(range(len(texts)))
            cached_results = []

        # Generate embeddings for uncached texts
        if uncached_texts:
            if self._use_local or not self.service_url:
                embeddings = self._encode_local(uncached_texts)
            else:
                embeddings = self._encode_http(uncached_texts)

            # Cache new embeddings
            if use_cache:
                for text, emb in zip(uncached_texts, embeddings):
                    self._cache_embedding(text, emb)

            # Merge with cached results
            if cached_results:
                all_results = cached_results + list(zip(uncached_indices, embeddings))
                result = np.array([emb for _, emb in sorted(all_results)])
            else:
                result = embeddings
        else:
            result = np.array([emb for _, emb in sorted(cached_results)])

        return result[0] if single_input else result

    def _encode_http(self, texts: List[str]) -> np.ndarray:
        """Encode texts using the HTTP embedding service."""
        import httpx

        start = time.perf_counter()
        try:
            client = self._get_http_client()
            response = client.post(
                f"{self.service_url}/embed",
                json={"texts": texts},
            )
            response.raise_for_status()

            data = response.json()
            embeddings = np.array(data["embeddings"])

            elapsed = (time.perf_counter() - start) * 1000
            logger.debug(f"HTTP embedding: {len(texts)} texts in {elapsed:.1f}ms (service: {data.get('processing_time_ms', 0):.1f}ms)")

            return embeddings

        except httpx.HTTPStatusError as e:
            logger.error(f"Embedding service HTTP error: {e.response.status_code} - {e.response.text}")
            if self.fallback_to_local:
                logger.warning("Falling back to local model")
                self._use_local = True
                return self._encode_local(texts)
            raise

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning(f"Embedding service connection failed: {e}")
            if self.fallback_to_local:
                logger.warning("Falling back to local model")
                self._use_local = True
                return self._encode_local(texts)
            raise

    def _encode_local(self, texts: List[str]) -> np.ndarray:
        """Encode texts using local model."""
        start = time.perf_counter()
        model = self._load_local_model()
        embeddings = model.encode(texts, convert_to_numpy=True)

        elapsed = (time.perf_counter() - start) * 1000
        logger.debug(f"Local embedding: {len(texts)} texts in {elapsed:.1f}ms")

        return embeddings

    def warmup(self):
        """
        Warm up the embedding service/model.

        In HTTP mode, makes a test request to ensure service is ready.
        In local mode, loads and warms up the model.
        """
        logger.info(f"Warming up embedding client (service_url={self.service_url})")

        if self.service_url and not self._use_local:
            try:
                _ = self.encode(["warmup query for embedding client"], use_cache=False)
                logger.info("Embedding service warmup successful")
            except Exception as e:
                logger.warning(f"Embedding service warmup failed: {e}")
                if self.fallback_to_local:
                    self._use_local = True
                    self._load_local_model()
        else:
            self._load_local_model()

    def health_check(self) -> dict:
        """Check if the embedding service is healthy."""
        if not self.service_url:
            return {
                "status": "local",
                "mode": "local",
                "model_loaded": self._local_model is not None,
            }

        try:
            import httpx
            client = self._get_http_client()
            response = client.get(f"{self.service_url}/health")
            response.raise_for_status()
            data = response.json()
            return {
                "status": data.get("status", "ok"),
                "mode": "http",
                "service_url": self.service_url,
                "model_loaded": data.get("model_loaded", False),
                "model_name": data.get("model_name"),
            }
        except Exception as e:
            return {
                "status": "error",
                "mode": "http",
                "service_url": self.service_url,
                "error": str(e),
                "fallback_available": self.fallback_to_local,
            }

    def close(self):
        """Close HTTP client if open."""
        if self._http_client is not None:
            self._http_client.close()
            self._http_client = None


# Global client instance
_embedding_client: Optional[EmbeddingClient] = None


def get_embedding_client(warmup: bool = False) -> EmbeddingClient:
    """
    Get the global embedding client instance.

    Args:
        warmup: If True, warm up the client (call at app startup)
    """
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = EmbeddingClient()
    if warmup:
        _embedding_client.warmup()
    return _embedding_client


def warmup_embedding_client():
    """Warm up the embedding client. Call at application startup."""
    get_embedding_client(warmup=True)
