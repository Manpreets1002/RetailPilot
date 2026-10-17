from pathlib import Path

from retailpilot.config.config import settings as app_settings

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = app_settings.django_debug
SECRET_KEY = app_settings.django_secret_key or ("dev-only-insecure-key" if DEBUG else None)
ALLOWED_HOSTS = app_settings.django_allowed_hosts.split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "django_db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# Sessions only hold the shopper id, so keep them in a signed cookie: no database needed,
# and they survive restarts and work across several instances (Cloud Run).
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
# Cloud Run (and most proxies) end HTTPS and forward plain HTTP with this header. Without it
# Django sees http:// and rejects HTTPS form posts as cross-origin (CSRF 403).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

RETAILPILOT_BACKEND_URL = app_settings.backend_url
MONGO_URI = app_settings.mongo_uri
MONGO_DB = app_settings.mongo_db
STALE_HOURS = app_settings.stale_hours
