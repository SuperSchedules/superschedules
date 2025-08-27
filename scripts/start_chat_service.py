#!/usr/bin/env python
# scripts/start_chat_service.py
from __future__ import annotations
import os, sys
from pathlib import Path
import uvicorn
from uvicorn.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# unbuffered stdout so prints show up immediately
os.environ.setdefault("PYTHONUNBUFFERED", "1")

def main():
    cfg = Config(
        "chat_service.app:app",
        host="0.0.0.0",
        port=8002,
        reload=False,          # keep off to avoid watcher spin
        loop="asyncio",        # <-- pure-Python loop
        http="h11",            # <-- pure-Python HTTP
        log_level="warning",
        access_log=False,
        timeout_keep_alive=75,
    )
    print(f"Project root: {ROOT}", flush=True)
    print(f"Loop: {cfg.loop}  HTTP: {cfg.http}", flush=True)
    uvicorn.run(cfg)

if __name__ == "__main__":
    main()

