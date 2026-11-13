"""Settings for build steps that need no secrets, e.g. collectstatic.

Used when building the image or installing on bare metal, before any
production configuration exists.
"""

from reeltime.settings.base import *  # noqa: F403 pylint: disable=wildcard-import,unused-wildcard-import
from reeltime.settings.base import MANIFEST_STORAGE
from reeltime.settings.base import STORAGES
from reeltime.settings.base import sqlite_database

SECRET_KEY = "build-only-not-used-at-runtime"  # noqa: S105  # nosec B105
DATABASES = {"default": sqlite_database(":memory:")}
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": MANIFEST_STORAGE}}
