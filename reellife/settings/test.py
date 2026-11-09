"""Settings for the medium test suite: in-memory SQLite, fast hashing."""

import pathlib
import tempfile

from reellife.settings.base import *  # noqa: F403 pylint: disable=wildcard-import,unused-wildcard-import
from reellife.settings.base import sqlite_database

SECRET_KEY = "test-only-insecure-key"  # noqa: S105  # nosec B105
ALLOWED_HOSTS = ["testserver"]
DATA_DIR = pathlib.Path(tempfile.gettempdir()) / "reellife-test"
DATABASES = {"default": sqlite_database(":memory:")}

BASE_URL = "http://testserver"
UPDATE_CHECK = False  # tests never call GitHub; they turn it on themselves
OIDC_ISSUER = "https://id.example.test"
OIDC_RP_CLIENT_ID = "reellife-test"
OIDC_RP_CLIENT_SECRET = "reellife-test-secret-long-enough-for-hs256"  # noqa: S105  # nosec B105
OIDC_GROUP_ADMIN = "reellife-admins"
OIDC_GROUP_EDITOR = "reellife-editors"
OIDC_GROUP_VIEWER = "reellife-viewers"
