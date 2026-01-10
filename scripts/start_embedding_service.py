#!/usr/bin/env python3
"""
Start the embedding microservice.

This service provides sentence embeddings via HTTP, allowing multiple
Django/FastAPI workers to share a single model instance.

Usage:
    python scripts/start_embedding_service.py
    python scripts/start_embedding_service.py --port 8003 --host 0.0.0.0

Environment:
    EMBEDDING_SERVICE_PORT - Port to listen on (default: 8003)
    EMBEDDING_SERVICE_HOST - Host to bind to (default: 0.0.0.0)
"""

import argparse
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


def main():
    parser = argparse.ArgumentParser(description="Start the embedding microservice")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("EMBEDDING_SERVICE_PORT", 8003)),
        help="Port to listen on (default: 8003)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("EMBEDDING_SERVICE_HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    args = parser.parse_args()

    print(f"Starting embedding service on {args.host}:{args.port}")
    print("This service provides /embed endpoint for sentence embeddings")
    print("Model: all-MiniLM-L6-v2 (384 dimensions)")
    print("-" * 50)

    import uvicorn

    uvicorn.run(
        "embedding_service.app:app",
        host=args.host,
        port=args.port,
        workers=1,  # IMPORTANT: Only 1 worker to share model
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
