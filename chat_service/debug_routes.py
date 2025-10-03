# chat_service/debug_routes.py
import asyncio, time, os
from fastapi import APIRouter
router = APIRouter()

def _info(t: asyncio.Task):
    fr = t.get_stack(limit=1)
    top = f"{fr[0].f_code.co_filename}:{fr[0].f_lineno}" if fr else "idle"
    return {"top": top, "coro": repr(t.get_coro())[:140], "done": t.done()}

@router.get("/chat/_debug/tasks")
async def tasks():
    return {"now": time.time(), "tasks": sorted((_info(t) for t in asyncio.all_tasks()),
                                               key=lambda x: (x["done"], x["top"]))}

@router.get("/chat/_debug/llm-config")
async def llm_config():
    """Debug endpoint to check LLM provider configuration"""
    from api.llm_service import get_llm_service
    from django.conf import settings

    llm_service = get_llm_service()

    return {
        "env_llm_provider": os.environ.get("LLM_PROVIDER", "not set"),
        "settings_llm_provider": getattr(settings, "LLM_PROVIDER", "not set"),
        "provider_class": llm_service.__class__.__name__,
        "primary_model": llm_service.primary_model,
        "backup_model": llm_service.backup_model,
    }

