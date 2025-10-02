import asyncio
from django.db import connection
from django.core.cache import cache
from ninja import Router

from api.services.health_aggregator import get_health_aggregator

router = Router()


@router.get("/live", auth=None)
def live(request):
    """Kubernetes liveness probe - is the app running?"""
    return {"status": "ok"}


@router.get("/ready", auth=None)
def ready(request):
    """Kubernetes readiness probe - can the app serve traffic?"""
    checks = {}
    ok = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["db"] = {"ok": True}
    except Exception as exc:
        checks["db"] = {"ok": False, "reason": str(exc)}
        ok = False

    try:
        cache.set("healthcheck", "1", 5)
        checks["cache"] = {"ok": cache.get("healthcheck") == "1"}
        if not checks["cache"]["ok"]:
            ok = False
    except Exception as exc:
        checks["cache"] = {"ok": False, "reason": str(exc)}
        ok = False

    status = "pass" if ok else "fail"
    return (200 if ok else 503, {"status": status, "checks": checks})


@router.get("/health/dashboard", auth=None)
async def health_dashboard(request):
    """
    Comprehensive health dashboard for all Superschedules services.
    Checks Django, Database, LLM, RAG, Collector, and Navigator.
    """
    aggregator = get_health_aggregator()
    health_data = await aggregator.check_all()

    # Return 200 if healthy, 503 if degraded
    status_code = 200 if health_data["status"] == "healthy" else 503

    return (status_code, health_data)
