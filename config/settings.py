"""Django settings for the Optimised Fuel Pricing Route project."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "planner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",  # route geometry compresses ~5-10x
    "django.middleware.common.CommonMiddleware",
]

# OpenStreetMap's tile servers require a Referer header; Django's default ("same-origin")
# strips it on cross-origin tile requests and the map tiles get blocked.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

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
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-planner",
        "TIMEOUT": 60 * 60 * 24,
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"planner": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")}},
}

# --- Fuel planner configuration -------------------------------------------------
DATA_DIR = BASE_DIR / "data"

FUEL_PLANNER = {
    # Free, key-less OSRM demo server. Swap for a self-hosted OSRM in production.
    "OSRM_BASE_URL": os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org"),
    # Only used when a location is not "lat,lon" and not found in the local gazetteer.
    "NOMINATIM_URL": os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search"),
    "HTTP_USER_AGENT": os.environ.get(
        "HTTP_USER_AGENT", "OptimisedFuelPricingRoute/1.0 (assessment project)"
    ),
    "HTTP_TIMEOUT_SECONDS": float(os.environ.get("HTTP_TIMEOUT_SECONDS", "20")),
    "MAX_RANGE_MILES": 500.0,
    "MILES_PER_GALLON": 10.0,
    # Stations within this distance of the route are candidates. If no feasible plan
    # exists, the corridor is widened step by step (no extra routing calls needed).
    "CORRIDOR_MILES": [10.0, 25.0, 50.0],
    # When starting with an empty tank, fill up at the cheapest station this close to the origin.
    "ORIGIN_RADIUS_MILES": 15.0,
    # Optimiser trade-off: a stop must save at least this many dollars to be worth making.
    # 0 = pure cheapest fuel (can produce 1-gallon "micro-stops"). Overridable per request.
    "STOP_PENALTY_DOLLARS": float(os.environ.get("STOP_PENALTY_DOLLARS", "5")),
    # Output geometry is decimated to one point every N miles (matching uses full geometry).
    "GEOMETRY_STEP_MILES": 0.5,
}
