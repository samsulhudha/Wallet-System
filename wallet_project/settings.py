import os
from pathlib import Path
from datetime import timedelta
import dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
dotenv.load_dotenv(BASE_DIR / '.env')

# Quick-start development settings - unsuitable for production
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-prod-ready-wallet-service-secret-key-change-in-production')

DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 't')

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party apps
    'rest_framework',
    'rest_framework_simplejwt',
    'drf_spectacular',

    # Domain apps
    'apps.accounts.apps.AccountsConfig',
    'apps.wallets.apps.WalletsConfig',
    'apps.ledger.apps.LedgerConfig',
    'apps.payments.apps.PaymentsConfig',
    'apps.risk.apps.RiskConfig',
    'apps.audit.apps.AuditConfig',
    'apps.common.apps.CommonConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'wallet_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
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

WSGI_APPLICATION = 'wallet_project.wsgi.application'
ASGI_APPLICATION = 'wallet_project.asgi.application'

# Database configuration
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'wallet_db'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', 'postgres'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.getenv('CONN_MAX_AGE', '60')),
        'ATOMIC_REQUESTS': False,  # We manage atomic transactions explicitly in service layer
    }
}

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'apps.common.pagination.StandardResultsSetPagination',
    'PAGE_SIZE': 20,
    'EXCEPTION_HANDLER': 'apps.common.exceptions.custom_exception_handler',
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# SimpleJWT Configuration
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=int(os.getenv('JWT_ACCESS_TOKEN_LIFETIME_MINUTES', '60'))),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=int(os.getenv('JWT_REFRESH_TOKEN_LIFETIME_DAYS', '7'))),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# drf-spectacular OpenAPI Settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'Enterprise Wallet & Ledger Service API',
    'DESCRIPTION': (
        'High-concurrency, double-entry financial wallet service featuring PostgreSQL row-level locking, '
        'immutable double-entry ledger, idempotency protection, daily limits, pluggable fraud engine, '
        'asynchronous mock banking with callback verification, and complete audit trail.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'TAGS': [
        {'name': 'Accounts', 'description': 'Authentication and user management'},
        {'name': 'Wallets', 'description': 'Customer wallet lifecycle and balance retrieval'},
        {'name': 'Transfers', 'description': 'P2P wallet-to-wallet transfers with row-level locking'},
        {'name': 'Top-ups', 'description': 'Wallet funding via payment gateway'},
        {'name': 'Withdrawals', 'description': 'Asynchronous withdrawal state machine and bank callbacks'},
        {'name': 'Statements', 'description': 'Ledger-backed transaction statements and export'},
        {'name': 'Admin - Wallets', 'description': 'Administrative freeze, unfreeze, and closure operations'},
        {'name': 'Admin - Reversals', 'description': 'Compensating ledger reversal of completed transfers'},
        {'name': 'Admin - Risk', 'description': 'Fraud engine decisions and flagged transaction inspection'},
    ],
    # Redoc CDN distribution and UI configuration (pins to stable 2.x to avoid Redocly 3.x white-screen bug)
    'REDOC_DIST': 'https://cdn.jsdelivr.net/npm/redoc@2.1.5',
    'REDOC_UI_SETTINGS': {
        'scrollYOffset': 50,
        'hideDownloadButton': False,
        'expandResponses': 'all',
        'theme': {
            'colors': {
                'primary': {
                    'main': '#4f46e5'
                }
            }
        }
    },
}

# Bank Integration Secrets & Configuration
BANK_WEBHOOK_SECRET = os.getenv('BANK_WEBHOOK_SECRET', 'super-secret-bank-callback-key-2026')

# Fraud Engine Thresholds (Configurable)
MAX_DAILY_TRANSFER_LIMIT_PAISE = int(os.getenv('MAX_DAILY_TRANSFER_LIMIT_PAISE', '10000000')) # 100,000 INR
VELOCITY_MAX_TRANSFERS = int(os.getenv('VELOCITY_MAX_TRANSFERS', '5'))
VELOCITY_WINDOW_MINUTES = int(os.getenv('VELOCITY_WINDOW_MINUTES', '5'))
NEW_ACCOUNT_AGE_DAYS = int(os.getenv('NEW_ACCOUNT_AGE_DAYS', '7'))
NEW_ACCOUNT_MAX_AMOUNT_PAISE = int(os.getenv('NEW_ACCOUNT_MAX_AMOUNT_PAISE', '5000000')) # 50,000 INR
RAPID_WITHDRAWAL_WINDOW_MINUTES = int(os.getenv('RAPID_WITHDRAWAL_WINDOW_MINUTES', '30'))
RAPID_WITHDRAWAL_MIN_TOPUP_PAISE = int(os.getenv('RAPID_WITHDRAWAL_MIN_TOPUP_PAISE', '1000000')) # 10,000 INR

# Logging Configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} [{name}:{lineno}] {message}',
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
        'level': 'INFO',
    },
    'loggers': {
        'django.db.backends': {
            'level': 'WARNING',  # Suppress noisy SQL logs unless debugging
            'handlers': ['console'],
            'propagate': False,
        },
        'apps': {
            'level': 'INFO',
            'handlers': ['console'],
            'propagate': False,
        },
    },
}

