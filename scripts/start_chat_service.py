#!/usr/bin/env python
from __future__ import annotations
import os, sys, argparse
from pathlib import Path
import uvicorn
from uvicorn import Config, Server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("PYTHONUNBUFFERED", "1")
os.environ.setdefault("WATCHFILES_FORCE_POLLING", "1")

def main():
    parser = argparse.ArgumentParser(description="Start Superschedules Chat Service")
    parser.add_argument(
        "--no-reload", 
        action="store_true", 
        help="Disable auto-reload to reduce CPU usage"
    )
    args = parser.parse_args()
    
    reload_enabled = not args.no_reload
    reload_msg = "🔄 Auto-reload enabled" if reload_enabled else "⚡ Auto-reload DISABLED (lower CPU usage)"
    
    config_params = {
        "app": "chat_service.app:app",
        "host": "0.0.0.0",
        "port": 8002,
        "loop": "asyncio",
        "http": "h11",
        "log_level": "info",
        "access_log": False,
        "timeout_keep_alive": 75,
    }
    
    if reload_enabled:
        config_params.update({
            "reload": True,
            "reload_dirs": ["chat_service", "api"],
            "reload_delay": 1.0,
        })
    
    cfg = Config(**config_params)
    
    print(f"Project root: {ROOT}", flush=True)
    print(f"Binding: {cfg.host}:{cfg.port}  Loop={cfg.loop} HTTP={cfg.http}", flush=True)
    print(f"{reload_msg}", flush=True)
    Server(cfg).run()

if __name__ == "__main__":
    main()

