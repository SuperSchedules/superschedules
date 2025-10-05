import os
from django.core.asgi import get_asgi_application

# Set Django settings before importing FastAPI app
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Get Django ASGI application (WhiteNoise middleware handles static files)
django_asgi_app = get_asgi_application()

# Import FastAPI app after Django is configured
from chat_service.app import app as fastapi_app

# Create combined ASGI application
async def application(scope, receive, send):
    if scope["type"] == "http":
        path = scope.get("path", "")
        # Route /api/v1/chat/* to FastAPI (streaming and non-streaming chat)
        # All other /api/v1/* routes go to Django (events, auth, etc.)
        if path.startswith("/api/v1/chat"):
            await fastapi_app(scope, receive, send)
        else:
            await django_asgi_app(scope, receive, send)
    elif scope["type"] == "websocket":
        # Route websockets to FastAPI for streaming
        await fastapi_app(scope, receive, send)
    else:
        await django_asgi_app(scope, receive, send)
