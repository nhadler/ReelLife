"""Emergency local admin sign-in for when PocketID is down.

Off unless REELLIFE_BREAKGLASS_ENABLED is true. One local admin, created
with `manage.py create_breakglass`, signs in with an Argon2-hashed
password plus a TOTP code. Turning the flag off also ends any break-glass
session, because get_user stops returning the user.
"""

from typing import Any, TypeGuard

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.http import HttpRequest
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts import models

USERNAME = "breakglass"


def is_breakglass_user(user: object) -> TypeGuard[models.User]:
    """Returns whether `user` is a local admin (no OIDC identity)."""
    return (
        isinstance(user, models.User)
        and user.oidc_sub is None
        and user.role == models.Role.ADMIN
        and user.has_usable_password()
    )


class Backend(ModelBackend):
    """Password + TOTP authentication for the break-glass admin only."""

    def authenticate(
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        otp_token: str | None = None,
        **kwargs: Any,
    ) -> models.User | None:
        """Returns the break-glass admin if password and code both match.

        Args:
            request: The sign-in request.
            username: The local admin's username.
            password: Its password.
            otp_token: The current code from its TOTP device.
            **kwargs: Ignored; other backends' credentials.

        Returns:
            The user, or None if break-glass is disabled or anything fails.
        """
        del kwargs
        if not settings.BREAKGLASS_ENABLED or not otp_token:
            return None
        user = super().authenticate(request, username, password)
        if not is_breakglass_user(user):
            return None
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if device is None or not device.verify_token(otp_token):
            return None
        return user

    def get_user(self, user_id: int) -> models.User | None:
        """Loads the session's user while break-glass stays enabled."""
        if not settings.BREAKGLASS_ENABLED:
            return None
        user = super().get_user(user_id)
        return user if is_breakglass_user(user) else None
