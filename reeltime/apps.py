"""App configs for the reeltime project package."""

from django.apps import AppConfig
from django.contrib.admin import apps as admin_apps


class ReelTimeConfig(AppConfig):
    """The project package as an app, for its management commands."""

    name = "reeltime"
    default = True


class ReelTimeAdminConfig(admin_apps.AdminConfig):
    """Installs ReelTimeAdminSite as the default admin site."""

    default = False
    default_site = "reeltime.admin.ReelTimeAdminSite"
