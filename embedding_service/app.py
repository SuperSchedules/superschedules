"""
Embedding microservice - provides sentence embeddings via HTTP.

This service loads the sentence-transformers model once and serves embeddings
to multiple clients (Django, FastAPI chat workers), avoiding duplicate model
loading across workers.

Run with: uvicorn embedding_service.app:app --host 0.0.0.0 --port 8003 --workers 1
"""

import logging
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Embedding Service",
    description="Sentence embedding microservice for EventZombie RAG",
    version="1.0.0",
)

# Global model instance - loaded once at startup
_model = None
_model_name = "all-MiniLM-L6-v2"


class EmbedRequest(BaseModel):
    """Request to generate embeddings for one or more texts."""
    texts: List[str] = Field(..., min_length=1, max_length=100, description="List of texts to embed (max 100)")


class EmbedResponse(BaseModel):
    """Response containing embeddings."""
    embeddings: List[List[float]] = Field(..., description="List of embedding vectors (384 dimensions each)")
    model: str = Field(..., description="Model used for embedding")
    processing_time_ms: float = Field(..., description="Time to generate embeddings in milliseconds")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    model_loaded: bool
    model_name: str


def get_model():
    """Get the loaded model, raising an error if not ready."""
    global _model
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet. Service is starting up.")
    return _model


@app.on_event("startup")
async def startup_event():
    """Load the model at startup to ensure it's warm."""
    global _model, _model_name

    logger.info(f"Loading sentence transformer model: {_model_name}")
    start = time.perf_counter()

    from sentence_transformers import SentenceTransformer
    _model = SentenceTransformer(_model_name)

    # Warmup inference to prime GPU/CPU caches
    _ = _model.encode(["warmup query"], convert_to_numpy=True)

    load_time = (time.perf_counter() - start) * 1000
    logger.info(f"Model loaded and warmed up in {load_time:.1f}ms")


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="ok" if _model is not None else "starting",
        model_loaded=_model is not None,
        model_name=_model_name,
    )


@app.post("/embed", response_model=EmbedResponse)
async def embed(request: EmbedRequest) -> EmbedResponse:
    """
    Generate embeddings for a list of texts.

    Returns 384-dimensional vectors for each input text using all-MiniLM-L6-v2.
    """
    model = get_model()

    start = time.perf_counter()
    embeddings = model.encode(request.texts, convert_to_numpy=True)
    processing_time = (time.perf_counter() - start) * 1000

    logger.info(f"Generated {len(request.texts)} embeddings in {processing_time:.1f}ms")

    return EmbedResponse(
        embeddings=embeddings.tolist(),
        model=_model_name,
        processing_time_ms=round(processing_time, 2),
    )


@app.post("/embed/single")
async def embed_single(text: str) -> dict:
    """
    Convenience endpoint for embedding a single text.

    Returns the embedding vector directly (384 floats).
    """
    model = get_model()

    start = time.perf_counter()
    embedding = model.encode([text], convert_to_numpy=True)[0]
    processing_time = (time.perf_counter() - start) * 1000

    return {
        "embedding": embedding.tolist(),
        "model": _model_name,
        "processing_time_ms": round(processing_time, 2),
    }
