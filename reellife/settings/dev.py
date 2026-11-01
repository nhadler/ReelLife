"""Local development settings. Never used in a deployment."""

from reellife import env
from reellife.settings.base import *  # noqa: F403 pylint: disable=wildcard-import,unused-wildcard-import
from reellife.settings.base import BASE_DIR
from reellife.settings.base import CONTENT_SECURITY_POLICY
from reellife.settings.base import INSTALLED_APPS
from reellife.settings.base import origin
from reellife.settings.base import sqlite_database

DEBUG = True
SECRET_KEY = "dev-only-insecure-key"  # noqa: S105  # nosec B105
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

DATA_DIR = BASE_DIR / env.get("REELLIFE_DATA_DIR", "data")
DATA_DIR.mkdir(exist_ok=True)
DATABASES = {"default": sqlite_database(DATA_DIR / "reellife.sqlite3")}

# Use http://127.0.0.1:8000, not localhost: the OIDC callback must land on
# the same host that set the session cookie.
BASE_URL = "http://127.0.0.1:8000"

# The fake provider from tests/fakes/oidc_provider.py, started by `just dev`.
OIDC_ISSUER = env.get("OIDC_ISSUER", "http://127.0.0.1:8001")
OIDC_RP_CLIENT_ID = env.get("OIDC_CLIENT_ID", "reellife-dev")
OIDC_RP_CLIENT_SECRET = env.get("OIDC_CLIENT_SECRET", "reellife-dev-secret")
CONTENT_SECURITY_POLICY["DIRECTIVES"]["form-action"].append(origin(OIDC_ISSUER))
BREAKGLASS_ENABLED = env.get_bool("REELLIFE_BREAKGLASS_ENABLED", default=False)
TIME_ZONE = env.get_time_zone("REELLIFE_TIME_ZONE", "America/Los_Angeles")

# Let WhiteNoise serve static files in development too: it sends
# max-age=0 under DEBUG, so browsers pick up edits to grid.js and app.css
# (runserver's own static view sends no Cache-Control at all).
INSTALLED_APPS = ["whitenoise.runserver_nostatic", *INSTALLED_APPS]
