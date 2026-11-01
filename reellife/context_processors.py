"""Template context shared by every page."""

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpRequest

from cues import models


def preferences(request: HttpRequest) -> dict[str, str]:
    """The colour theme and date formats from App Settings.

    Error pages render this too, so a database problem must not break
    them: they get the defaults.
    """
    del request
    try:
        settings_row = models.AppSettings.load()
    except DatabaseError:
        settings_row = models.AppSettings()
    return {"theme": settings_row.theme, **settings_row.date_formats()}


def account(request: HttpRequest) -> dict[str, str]:
    """The provider's account page for the profile menu, for OIDC users.

    PocketID's is /settings/account under the issuer; OIDC_ACCOUNT_URL
    overrides it for other providers.
    """
    if not getattr(request.user, "oidc_sub", None):
        return {}
    issuer = settings.OIDC_ISSUER.rstrip("/")
    return {
        "account_url": settings.OIDC_ACCOUNT_URL or f"{issuer}/settings/account"
    }
