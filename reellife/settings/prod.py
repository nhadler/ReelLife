"""Production settings, configured entirely from the environment.

deploy/README.md lists the variables. `manage.py check --deploy` must pass
with no warnings against this module.
"""

import pathlib
import urllib.parse

from django.core.exceptions import ImproperlyConfigured

from reellife import env
from reellife.settings.base import *  # noqa: F403 pylint: disable=wildcard-import,unused-wildcard-import
from reellife.settings.base import CONTENT_SECURITY_POLICY
from reellife.settings.base import MANIFEST_STORAGE
from reellife.settings.base import SILENCED_SYSTEM_CHECKS
from reellife.settings.base import STORAGES
from reellife.settings.base import origin
from reellife.settings.base import sqlite_database

DEBUG = False

SECRET_KEY = env.get("REELLIFE_SECRET_KEY")
ALLOWED_HOSTS = env.get_list("REELLIFE_ALLOWED_HOSTS")

BASE_URL = env.get("REELLIFE_BASE_URL").rstrip("/")
if urllib.parse.urlsplit(BASE_URL).scheme != "https":
    raise ImproperlyConfigured("REELLIFE_BASE_URL must be an https:// URL")
CSRF_TRUSTED_ORIGINS = [BASE_URL]

OIDC_ISSUER = env.get("OIDC_ISSUER").rstrip("/")
if urllib.parse.urlsplit(OIDC_ISSUER).scheme != "https":
    raise ImproperlyConfigured("OIDC_ISSUER must be an https:// URL")
OIDC_RP_CLIENT_ID = env.get("OIDC_CLIENT_ID")
OIDC_RP_CLIENT_SECRET = env.get("OIDC_CLIENT_SECRET")
# Logging out posts a form that redirects to the provider's end-session
# endpoint, so the issuer must be an allowed form-action.
CONTENT_SECURITY_POLICY["DIRECTIVES"]["form-action"].append(origin(OIDC_ISSUER))

BREAKGLASS_ENABLED = env.get_bool("REELLIFE_BREAKGLASS_ENABLED", default=False)

# STATIC_ROOT is collected at install time (reellife.settings.build).
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": MANIFEST_STORAGE}}

DATA_DIR = pathlib.Path(env.get("REELLIFE_DATA_DIR", "/var/lib/reellife"))
DATABASES = {"default": sqlite_database(DATA_DIR / "reellife.sqlite3")}

# Only trust X-Forwarded-Proto when a proxy in front sets it; otherwise a
# client could claim HTTPS.
TRUST_PROXY = env.get_bool("REELLIFE_TRUST_PROXY", default=False)
if TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = False

SECURE_SSL_REDIRECT = True
# Health probes come from Docker and systemd over plain HTTP on loopback.
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
SECURE_HSTS_SECONDS = 365 * 24 * 60 * 60
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
# Preload is off by default: it is hard to undo and needs the
# whole domain on HTTPS. Silencing the reminder keeps `check --deploy` at
# zero warnings without changing any protection.
SILENCED_SYSTEM_CHECKS = [*SILENCED_SYSTEM_CHECKS, "security.W021"]

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# __Host- binds the cookie to this exact origin over HTTPS.
SESSION_COOKIE_NAME = "__Host-sessionid"
CSRF_COOKIE_NAME = "__Host-csrftoken"
