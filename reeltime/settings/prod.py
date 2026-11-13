"""Production settings, configured entirely from the environment.

deploy/README.md lists the variables. `manage.py check --deploy` must pass
with no warnings against this module.
"""

import pathlib
import urllib.parse

from django.core.exceptions import ImproperlyConfigured

from reeltime import env
from reeltime.settings.base import *  # noqa: F403 pylint: disable=wildcard-import,unused-wildcard-import
from reeltime.settings.base import CONTENT_SECURITY_POLICY
from reeltime.settings.base import MANIFEST_STORAGE
from reeltime.settings.base import SILENCED_SYSTEM_CHECKS
from reeltime.settings.base import STORAGES
from reeltime.settings.base import database_file
from reeltime.settings.base import origin
from reeltime.settings.base import sqlite_database

DEBUG = False

SECRET_KEY = env.get("REELTIME_SECRET_KEY")
ALLOWED_HOSTS = env.get_list("REELTIME_ALLOWED_HOSTS")

BASE_URL = env.get("REELTIME_BASE_URL").rstrip("/")
if urllib.parse.urlsplit(BASE_URL).scheme != "https":
    raise ImproperlyConfigured("REELTIME_BASE_URL must be an https:// URL")
CSRF_TRUSTED_ORIGINS = [BASE_URL]

OIDC_ISSUER = env.get("OIDC_ISSUER").rstrip("/")
if urllib.parse.urlsplit(OIDC_ISSUER).scheme != "https":
    raise ImproperlyConfigured("OIDC_ISSUER must be an https:// URL")
OIDC_RP_CLIENT_ID = env.get("OIDC_CLIENT_ID")
OIDC_RP_CLIENT_SECRET = env.get("OIDC_CLIENT_SECRET")
# Logging out posts a form that redirects to the provider's end-session
# endpoint, so the issuer must be an allowed form-action.
CONTENT_SECURITY_POLICY["DIRECTIVES"]["form-action"].append(origin(OIDC_ISSUER))

BREAKGLASS_ENABLED = env.get_bool("REELTIME_BREAKGLASS_ENABLED", default=False)

# STATIC_ROOT is collected at install time (reeltime.settings.build).
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": MANIFEST_STORAGE}}

DATA_DIR = pathlib.Path(env.get("REELTIME_DATA_DIR", "/var/lib/reeltime"))
DATABASES = {"default": sqlite_database(database_file(DATA_DIR))}

# Only trust X-Forwarded-Proto when a proxy in front sets it; otherwise a
# client could claim HTTPS.
TRUST_PROXY = env.get_bool("REELTIME_TRUST_PROXY", default=False)
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
