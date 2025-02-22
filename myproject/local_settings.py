# local_settings.py
import os
import dj_database_url

# Local development settings
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'calendar_db',
        'USER': os.environ.get('DB_USER', 'chinedu'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}

# Override database configuration for Render
if 'RENDER' in os.environ:
    DATABASES['default'] = dj_database_url.config(
        default=os.environ.get('DATABASE_URL'),
        conn_max_age=600,
        ssl_require=True
    )

# Site URL for generating authentication links
SITE_URL = os.environ.get('SITE_URL', 'http://localhost:8000')

# Debug settings
DEBUG = os.environ.get('DEBUG', 'True') == 'True'

# Allowed hosts
ALLOWED_HOSTS = [
    '7ec3-71-163-137-36.ngrok-free.app',
    'localhost',
    '127.0.0.1',
    'localhost:8000',
    'b54f-2600-4040-4651-e600-ad65-4f90-1b03-b82d.ngrok-free.app'
]

# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}