from copy import deepcopy
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'change-me')
DEBUG = os.environ.get('DEBUG', 'True') == 'True'

# ALLOWED_HOSTS configuration
# Can be overridden with ALLOWED_HOSTS env var (comma-separated list)
if allowed_hosts_env := os.environ.get('ALLOWED_HOSTS'):
    ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_env.split(',')]
else:
    # Allow all hosts since we're behind an ALB that acts as the security perimeter
    # The ALB only forwards requests to approved domains (eventzombie.com, api.eventzombie.com, etc.)
    # This also allows ALB health checks which come from internal IPs
    ALLOWED_HOSTS = ['*']

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

CSRF_TRUSTED_ORIGINS = [
    "https://eventzombie.com",
    "https://www.eventzombie.com",
    "https://admin.eventzombie.com",
    "https://api.eventzombie.com",
    "http://localhost:5173",
    "http://localhost:5174",
]

INSTALLED_APPS = [
    'grappelli',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'rest_framework_simplejwt',
    'ninja',
    'django_celery_beat',
    'django_celery_results',
    'events',
    'venues',
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Split this out so it's not like this on production once postgres is not local. 
DB_HOST = os.environ.get('DB_HOST', '')
DB_PORT = os.environ.get('DB_PORT', '5432')
DB_NAME = os.environ.get('DB_NAME', 'superschedules')
DB_USER = os.environ.get('DB_USER', 'gregk')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')

# If no host specified, assume local peer authentication
if not DB_HOST:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': DB_NAME,
            'USER': DB_USER,
            'PASSWORD': '',  # Empty for peer auth
            'HOST': '',      # Unix socket
            'PORT': '',      # Default socket
        }
    }
else:
    # Remote database configuration
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': DB_NAME,
            'USER': DB_USER,
            'PASSWORD': DB_PASSWORD,
            'HOST': DB_HOST,
            'PORT': DB_PORT,
        }
    }
# Temporary: read-only alias pointing at your old SQLite file
SQLITE_PATH = Path(BASE_DIR) / "db.sqlite3"   # adjust path

DATABASES["sqlite_tmp"] = cfg = deepcopy(DATABASES["default"])
cfg.update({
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": str(SQLITE_PATH),
    "USER": "",
    "PASSWORD": "",
    "HOST": "",
    "PORT": "",
})


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST framework configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=5),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
}

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "https://admin.eventzombie.com",
    "https://eventzombie.com",
    "https://www.eventzombie.com",
    "https://api.eventzombie.com",
]

# Add ALB host dynamically if provided
if alb_host := os.environ.get('ALB_HOST'):
    CORS_ALLOWED_ORIGINS.append(f"http://{alb_host}")
    CORS_ALLOWED_ORIGINS.append(f"https://{alb_host}")

CSRF_TRUSTED_ORIGINS = [
    "https://eventzombie.com",
    "https://www.eventzombie.com",
    "https://admin.eventzombie.com",
    "https://api.eventzombie.com",
    "http://localhost:5173",
    "http://localhost:5174",
]

# Add ALB host to CSRF trusted origins if provided
if alb_host := os.environ.get('ALB_HOST'):
    CSRF_TRUSTED_ORIGINS.append(f"http://{alb_host}")
    CSRF_TRUSTED_ORIGINS.append(f"https://{alb_host}")


# Custom test runner for pgvector support
TEST_RUNNER = 'test_runner.PgVectorTestRunner'

# Email configuration for password reset
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@example.com")

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")
PASSWORD_RESET_TIMEOUT = int(os.environ.get("PASSWORD_RESET_TIMEOUT", 3600))

# Service URLs for health checks
COLLECTOR_URL = os.environ.get("COLLECTOR_URL", "http://localhost:8001")
NAVIGATOR_URL = os.environ.get("NAVIGATOR_URL", "http://localhost:8004")

# LLM Configuration
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")  # 'ollama' or 'bedrock'

# Ollama configuration (for local development)
LLM_PRIMARY_MODEL = os.environ.get("LLM_PRIMARY_MODEL", "deepseek-llm:7b")
LLM_BACKUP_MODEL = os.environ.get("LLM_BACKUP_MODEL", "llama3.2:3b")

# AWS Bedrock configuration (for production)
AWS_BEDROCK_REGION = os.environ.get("AWS_BEDROCK_REGION", "us-east-1")
AWS_BEDROCK_MODEL_ID = os.environ.get(
    "AWS_BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)
AWS_BEDROCK_BACKUP_MODEL_ID = os.environ.get(
    "AWS_BEDROCK_BACKUP_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0"
)

# Logging Configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.environ.get('LOG_LEVEL', 'INFO'),
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'api': {
            'handlers': ['console'],
            'level': os.environ.get('LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'events': {
            'handlers': ['console'],
            'level': os.environ.get('LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'chat_service': {
            'handlers': ['console'],
            'level': os.environ.get('LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
    },
}

# Celery Configuration
# Using AWS SQS as broker for production reliability
# Database broker was causing issues with message parsing and isn't recommended for production

# SQS broker URL format: sqs://aws_access_key_id:aws_secret_access_key@
# When running on EC2 with IAM role, credentials are automatic (no need to specify in URL)
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

if os.environ.get('USE_SQS_BROKER', 'True') == 'True':
    # Production: Use SQS with boto3 transport (credentials via IAM role)
    # boto3 is used automatically since pycurl is not installed
    CELERY_BROKER_URL = f'sqs://'
    CELERY_BROKER_TRANSPORT_OPTIONS = {
        'region': AWS_REGION,
        'queue_name_prefix': 'superschedules-',
        'visibility_timeout': 3600,  # 1 hour
        'polling_interval': 1,  # Poll every second
        'wait_time_seconds': 20,  # Enable long polling for efficiency
    }
else:
    # Local development fallback: Use database broker
    from urllib.parse import quote_plus
    if DB_HOST:
        encoded_password = quote_plus(DB_PASSWORD)
        CELERY_BROKER_URL = f'sqla+postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
    else:
        CELERY_BROKER_URL = f'sqla+postgresql://{DB_USER}@/{DB_NAME}'

# Results stored via django-celery-results
CELERY_RESULT_BACKEND = 'django-db'

# Celery Beat scheduler uses database
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Task serialization
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# Task settings
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes max per task
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # Soft limit at 25 minutes

# Result expiration (7 days)
CELERY_RESULT_EXPIRES = 60 * 60 * 24 * 7

# Worker settings
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # One task at a time for rate-limited APIs
CELERY_WORKER_CONCURRENCY = 2  # Conservative for database-backed broker

# Set default queue to 'default' (not Celery's internal 'celery' queue)
# This ensures all unrouted tasks go to 'default' which the worker listens on
CELERY_TASK_DEFAULT_QUEUE = 'default'

# Task routes for prioritization
CELERY_TASK_ROUTES = {
    'events.tasks.generate_embedding': {'queue': 'embeddings'},
    'venues.tasks.geocode_venue': {'queue': 'geocoding'},
    'venues.tasks.geocode_venue_task': {'queue': 'geocoding'},
    'events.tasks.process_scraping_job': {'queue': 'scraping'},
    # Catch-all routes for any other tasks
    'events.tasks.*': {'queue': 'default'},
    'venues.tasks.*': {'queue': 'default'},
}
