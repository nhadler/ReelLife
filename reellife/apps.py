"""App configs for the reellife project package."""

from django.apps import AppConfig
from django.contrib.admin import apps as admin_apps


class ReelLifeConfig(AppConfig):
    """The project package as an app, for its management commands."""

    name = "reellife"
    default = True


class ReelLifeAdminConfig(admin_apps.AdminConfig):
    """Installs ReelLifeAdminSite as the default admin site."""

    default = False
    default_site = "reellife.admin.ReelLifeAdminSite"
