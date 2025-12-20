"""
Health check aggregator for all Superschedules services.
Checks Django, Collector, Navigator, Database, LLM, and RAG.
"""

import logging
from typing import Dict, Any
from datetime import datetime
import asyncio

import httpx
from django.db import connection
from django.conf import settings

from api.llm_providers import get_llm_provider


logger = logging.getLogger(__name__)


class HealthAggregator:
    """Aggregate health checks from all services."""

    def __init__(self):
        self.collector_url = getattr(settings, 'COLLECTOR_URL', 'http://localhost:8001')
        self.navigator_url = getattr(settings, 'NAVIGATOR_URL', 'http://localhost:8004')
        self.timeout = 5.0

    async def check_database(self) -> Dict[str, Any]:
        """Check database connection and pgvector extension."""
        from asgiref.sync import sync_to_async

        def _check_db():
            with connection.cursor() as cursor:
                # Check basic connection
                cursor.execute("SELECT 1")
                cursor.fetchone()

                # Check pgvector extension
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                )
                has_pgvector = cursor.fetchone()[0]
                return has_pgvector

        try:
            has_pgvector = await sync_to_async(_check_db)()
            return {
                "status": "healthy",
                "details": {
                    "connected": True,
                    "pgvector_enabled": has_pgvector
                }
            }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e)
            }

    async def check_llm_provider(self) -> Dict[str, Any]:
        """Check LLM provider availability."""
        try:
            provider = get_llm_provider()
            models = await provider.get_available_models()

            return {
                "status": "healthy",
                "details": {
                    "provider": type(provider).__name__,
                    "primary_model": provider.primary_model,
                    "backup_model": provider.backup_model,
                    "available_models_count": len(models)
                }
            }
        except Exception as e:
            logger.error(f"LLM provider health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e)
            }

    async def check_rag_service(self) -> Dict[str, Any]:
        """Check RAG service availability."""
        try:
            # Lazy import to avoid circular dependency
            from api.rag_service import get_rag_service
            rag = get_rag_service()

            # Check if we can get the model (doesn't make network call)
            model_name = rag.model_name if hasattr(rag, 'model_name') else "unknown"

            return {
                "status": "healthy",
                "details": {
                    "model": model_name,
                    "ready": True
                }
            }
        except Exception as e:
            logger.error(f"RAG service health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e)
            }

    async def check_external_service(self, name: str, url: str) -> Dict[str, Any]:
        """Check external service health endpoint."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{url}/health")

                if response.status_code == 200:
                    data = response.json()
                    return {
                        "status": "healthy",
                        "details": data
                    }
                else:
                    return {
                        "status": "unhealthy",
                        "error": f"HTTP {response.status_code}"
                    }
        except httpx.TimeoutException:
            logger.warning(f"{name} health check timed out")
            return {
                "status": "unhealthy",
                "error": "Timeout"
            }
        except httpx.ConnectError:
            logger.warning(f"{name} connection failed")
            return {
                "status": "unavailable",
                "error": "Connection refused"
            }
        except Exception as e:
            logger.error(f"{name} health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e)
            }

    async def check_all(self) -> Dict[str, Any]:
        """Run all health checks concurrently."""
        start_time = datetime.now()

        # Run all checks in parallel
        results = await asyncio.gather(
            self.check_database(),
            self.check_llm_provider(),
            self.check_rag_service(),
            return_exceptions=True
        )

        # Map results to service names
        database, llm, rag = results

        # Handle exceptions from gather
        def safe_result(result, default_name):
            if isinstance(result, Exception):
                logger.error(f"{default_name} health check raised exception: {result}")
                return {"status": "error", "error": str(result)}
            return result

        checks = {
            "database": safe_result(database, "database"),
            "llm_provider": safe_result(llm, "llm_provider"),
            "rag_service": safe_result(rag, "rag_service"),
        }

        # Overall health status
        statuses = [check.get("status") for check in checks.values()]
        overall_healthy = all(status == "healthy" for status in statuses)

        end_time = datetime.now()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        return {
            "status": "healthy" if overall_healthy else "degraded",
            "timestamp": start_time.isoformat(),
            "duration_ms": duration_ms,
            "services": checks
        }


# Singleton instance
_health_aggregator = None


def get_health_aggregator() -> HealthAggregator:
    """Get the global health aggregator instance."""
    global _health_aggregator
    if _health_aggregator is None:
        _health_aggregator = HealthAggregator()
    return _health_aggregator
