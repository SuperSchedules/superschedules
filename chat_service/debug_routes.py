# chat_service/debug_routes.py
import asyncio, time
from fastapi import APIRouter
router = APIRouter()

def _info(t: asyncio.Task):
    fr = t.get_stack(limit=1)
    top = f"{fr[0].f_code.co_filename}:{fr[0].f_lineno}" if fr else "idle"
    return {"top": top, "coro": repr(t.get_coro())[:140], "done": t.done()}

@router.get("/_debug/tasks")
async def tasks():
    return {"now": time.time(), "tasks": sorted((_info(t) for t in asyncio.all_tasks()),
                                               key=lambda x: (x["done"], x["top"]))}

