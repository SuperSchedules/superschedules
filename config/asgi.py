import os
from django.core.asgi import get_asgi_application

# Set Django settings before importing FastAPI app
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Get Django ASGI application and wrap with WhiteNoise
from whitenoise import WhiteNoise
from django.conf import settings

django_asgi_app = get_asgi_application()

# WhiteNoise expects URL prefixes without leading slash
static_prefix = settings.STATIC_URL.lstrip('/') or None
django_asgi_app = WhiteNoise(django_asgi_app, root=str(settings.STATIC_ROOT), prefix=static_prefix)

# Import FastAPI app after Django is configured
from chat_service.app import app as fastapi_app

# Create combined ASGI application
async def application(scope, receive, send):
    if scope["type"] == "http":
        path = scope.get("path", "")
        # Route FastAPI paths
        if (path.startswith("/chat") or
            path.startswith("/debug") or
            path == "/health" or
            path.startswith("/docs") or
            path.startswith("/openapi")):
            await fastapi_app(scope, receive, send)
        else:
            await django_asgi_app(scope, receive, send)
    elif scope["type"] == "websocket":
        # Route websockets to FastAPI for streaming
        await fastapi_app(scope, receive, send)
    else:
        await django_asgi_app(scope, receive, send)
