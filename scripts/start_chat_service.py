#!/usr/bin/env python
from __future__ import annotations
import os, sys
from pathlib import Path
import uvicorn
from uvicorn import Config, Server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("PYTHONUNBUFFERED", "1")

def main():
    cfg = Config(
        "chat_service.app:app",
        host="0.0.0.0",
        port=8002,
        reload=False,
        loop="asyncio",
        http="h11",
        log_level="info",
        access_log=False,
        timeout_keep_alive=75,
    )
    print(f"Project root: {ROOT}", flush=True)
    print(f"Binding: {cfg.host}:{cfg.port}  Loop={cfg.loop} HTTP={cfg.http}", flush=True)
    Server(cfg).run()

if __name__ == "__main__":
    main()

