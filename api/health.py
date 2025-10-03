from django.db import connection
from django.core.cache import cache
from ninja import Router

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
