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
    # Default hosts for development and production
    ALLOWED_HOSTS = [
        'localhost',
        '127.0.0.1',
        'eventzombie.com',
        'www.eventzombie.com',
        'api.eventzombie.com',
        'admin.eventzombie.com',
        'superschedules-prod-alb-920320173.us-east-1.elb.amazonaws.com',  # For ALB health checks
    ]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

CSRF_TRUSTED_ORIGINS = [
    "https://eventzombie.com",
    "https://www.eventzombie.com",
    "https://admin.eventzombie.com",
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
    'events',
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
]

# Add ALB host dynamically if provided
if alb_host := os.environ.get('ALB_HOST'):
    CORS_ALLOWED_ORIGINS.append(f"http://{alb_host}")
    CORS_ALLOWED_ORIGINS.append(f"https://{alb_host}")

CSRF_TRUSTED_ORIGINS = [
    "https://eventzombie.com",
    "https://www.eventzombie.com",
    "https://admin.eventzombie.com",
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
    'loggers': {
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
