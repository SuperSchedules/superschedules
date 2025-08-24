#!/usr/bin/env python
"""
Startup script for the FastAPI chat service.
Run this alongside your Django server for streaming chat functionality.
"""
import uvicorn
import sys
import os

# Add current directory to path so Django can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    print("🚀 Starting FastAPI Chat Service...")
    print("📡 Service will be available at: http://localhost:8002")
    print("📚 API docs at: http://localhost:8002/docs")
    print("🔄 Make sure your Django server is running on port 8000")
    print("💾 Collector service on port 8001")
    print()
    
    uvicorn.run(
        "chat_service.app:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level="info"
    )
