"""Settings shared by every environment.

Environment-specific modules import from here and override. Security
settings that must hold everywhere live here so dev and tests exercise
them too.
"""

import pathlib
from typing import Any
import urllib.parse

from csp import constants as csp

from reellife import env

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent

DEBUG = False

INSTALLED_APPS = [
    "reellife.apps.ReelLifeAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "csp",
    "django_ratelimit",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "simple_history",
    "reellife",
    "accounts",
    "cues",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves collected static files with far-future caching.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "csp.middleware.CSPMiddleware",
    "reellife.middleware.PermissionsPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.sessions.SessionTimeoutMiddleware",
    # Every view needs a signed-in user unless marked login_not_required.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    # Then every view needs a role; see accounts/authz.py.
    "accounts.authz.RoleMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Records who made each change in the cue and picture history.
    "simple_history.middleware.HistoryRequestMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_ratelimit.middleware.RatelimitMiddleware",
]

ROOT_URLCONF = "reellife.urls"
WSGI_APPLICATION = "reellife.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "csp.context_processors.nonce",
                "reellife.context_processors.preferences",
                "reellife.context_processors.account",
            ],
        },
    },
]


def sqlite_database(path: pathlib.Path | str) -> dict[str, object]:
    """Returns a DATABASES entry for SQLite in WAL mode.

    Args:
        path: The database file, or ':memory:'.

    Returns:
        The settings dict for DATABASES['default'].
    """
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": path,
        "OPTIONS": {
            # IMMEDIATE avoids "database is locked" when two writers race.
            "transaction_mode": "IMMEDIATE",
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA busy_timeout=5000;"
            ),
        },
    }


# Whether a reverse proxy in front sets X-Forwarded-For/-Proto. Production
# reads REELLIFE_TRUST_PROXY; everywhere else nothing sits in front.
TRUST_PROXY = False

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "accounts.oidc.Backend",
    # Inert unless REELLIFE_BREAKGLASS_ENABLED is true.
    "accounts.breakglass.Backend",
]
BREAKGLASS_ENABLED = False

# Only the break-glass admin has a password.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]
_VALIDATION = "django.contrib.auth.password_validation"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": f"{_VALIDATION}.MinimumLengthValidator",
        "OPTIONS": {"min_length": 16},
    },
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/auth/login/"

# OIDC sign-in. Issuer and client credentials are set per
# environment; endpoints come from the issuer's discovery document.
# offline_access asks for a refresh token, so the 5-minute session check
# (accounts.oidc.check_session) can renew an expired access token instead
# of ending the session.
OIDC_RP_SCOPES = "openid profile email groups offline_access"
OIDC_AUTHENTICATION_CALLBACK_URL = "accounts:oidc_callback"
OIDC_USE_PKCE = True
OIDC_PKCE_CODE_CHALLENGE_METHOD = "S256"
OIDC_USE_NONCE = True
# Kept server-side in the session: the ID token for the logout
# id_token_hint, the access token (and a refresh token, when the provider
# issues one) for the session checks in accounts/sessions.py.
OIDC_STORE_ID_TOKEN = True
OIDC_STORE_ACCESS_TOKEN = True
OIDC_TIMEOUT = 10
OIDC_GROUP_ADMIN = env.get("OIDC_GROUP_ADMIN", "reellife-admins")
OIDC_GROUP_EDITOR = env.get("OIDC_GROUP_EDITOR", "reellife-editors")
OIDC_GROUP_VIEWER = env.get("OIDC_GROUP_VIEWER", "reellife-viewers")
# Where the profile menu's Account item goes; blank means PocketID's own
# account page under OIDC_ISSUER.
OIDC_ACCOUNT_URL = env.get("OIDC_ACCOUNT_URL", "")

# Where Help links to this instance's source code. The AGPL asks anyone
# who runs a modified copy for others to offer them its source, so a
# fork points this at its own repository.
REELLIFE_SOURCE_URL = env.get(
    "REELLIFE_SOURCE_URL", "https://github.com/nhadler/reellife"
)


def origin(url: str) -> str:
    """Returns the scheme://host[:port] part of a URL."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
# Times are stored in UTC and shown in this zone.
TIME_ZONE = env.get_time_zone("REELLIFE_TIME_ZONE", "UTC")
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Production and image builds use WhiteNoise's hashed, compressed files
# (reellife.settings.build); elsewhere plain files, no collectstatic.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}
MANIFEST_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "reellife.logs.JsonFormatter"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
        },
    },
    "root": {"handlers": ["stdout"], "level": "INFO"},
    # PDF rendering logs every font subset at INFO; warnings still show.
    "loggers": {
        name: {"level": "WARNING"} for name in ("weasyprint", "fontTools")
    },
}

# Upload limit for imports.
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
# An import preview posts a field per row it includes and Conform one per
# cue ticked; 1,000 (Django's default) is the project size we plan for.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5_000
FILE_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MEMORY_SIZE
# The largest request body the web app reads at all (reellife/asgi.py):
# a 10 MB import plus its multipart framing. Restoring a backup, as an
# admin, allows that page's own limit instead.
REQUEST_BODY_MAX = DATA_UPLOAD_MAX_MEMORY_SIZE + 2 * 1024 * 1024

# Headers and cookies. Cookie Secure flags and HSTS are
# production-only because dev runs over plain HTTP.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
# Lax, not Strict: the OIDC callback is a cross-site top-level redirect.
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Session lifetimes, enforced by accounts.sessions.SessionTimeoutMiddleware.
# After either, the user signs in again through PocketID.
SESSION_IDLE_TIMEOUT = 8 * 60 * 60
SESSION_ABSOLUTE_TIMEOUT = 7 * 24 * 60 * 60
# How often a signed-in session asks PocketID whether its user is still
# allowed in (groups, account still enabled); see accounts.oidc.
SESSION_CHECK_INTERVAL = 5 * 60
SESSION_COOKIE_AGE = SESSION_ABSOLUTE_TIMEOUT

# Strict CSP: no inline scripts or event handlers anywhere.
# Page data goes in <script type="application/json"> blocks. Styles allow a
# per-request nonce for the <style> elements AG Grid injects. Environments
# that know the OIDC issuer add it to form-action, because logging out
# posts a form that redirects to the provider.
CONTENT_SECURITY_POLICY: dict[str, dict[str, list[Any]]] = {
    "DIRECTIVES": {
        "default-src": [csp.SELF],
        "script-src": [csp.SELF],
        "style-src": [csp.SELF, csp.NONCE],
        "img-src": [csp.SELF, "data:"],
        "connect-src": [csp.SELF],
        "frame-ancestors": [csp.NONE],
        "form-action": [csp.SELF],
        "base-uri": [csp.NONE],
        "object-src": [csp.NONE],
    },
}

# Nightly maintenance (reellife.maintenance): snapshots kept, audit log
# retention, and the hour (UTC) the scheduler runs it.
SNAPSHOT_KEEP = 14
AUDIT_RETENTION_DAYS = 365
NIGHTLY_HOUR_UTC = 3

# Rate-limit counters and the cached discovery document live in SQLite so
# every gunicorn worker shares them without another service.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "reellife_cache",
    },
}

# Rate limits, per client IP (reellife.net.client_ip).
RATELIMIT_VIEW = "reellife.views.rate_limited"
RATELIMIT_LOGIN = "30/m"
RATELIMIT_BREAKGLASS = "5/h"
# Grid edits and other API writes, per signed-in user.
RATELIMIT_API_WRITES = "300/m"
# Each import step re-reads the uploaded file, so allow fewer.
RATELIMIT_IMPORTS = "30/m"
RATELIMIT_EXPORTS = "60/m"
# MCP requests per minute (reellife/asgi.py): for one signed-in user's
# client (PocketID subject and OAuth client, whichever token it sends),
# and for one client address (reellife.net) before any token is checked,
# counting every request. Behind a proxy without REELLIFE_TRUST_PROXY
# every client shares the proxy's address and so the second limit.
RATELIMIT_MCP_PER_MINUTE = 120
RATELIMIT_MCP_PER_ADDRESS_PER_MINUTE = 300
# Backups read or replace everything; a handful an hour is plenty.
RATELIMIT_BACKUPS = "10/h"
# Calendar feeds, per client address: apps refresh every few minutes at
# most, so this only stops someone guessing links.
RATELIMIT_CALENDAR = "60/m"

# django-ratelimit warns (E003, W001) that the database cache's
# increments aren't atomic: a burst of concurrent requests can slightly
# exceed a limit. That is acceptable for throttling sign-in, and avoids
# running Redis or Memcached.
SILENCED_SYSTEM_CHECKS: list[str] = [
    "django_ratelimit.E003",
    "django_ratelimit.W001",
]
