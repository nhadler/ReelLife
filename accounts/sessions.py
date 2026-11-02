"""Session lifetimes: 8 hours idle, 7 days absolute.

Django's own expiry slides forward whenever the session is saved, so the
absolute limit is enforced here from the sign-in time. Every
SESSION_CHECK_INTERVAL an OIDC session also asks PocketID whether its
user may still use ReelLife (accounts.oidc.check_session): removed from
the ReelLife groups, disabled, or tokens that can't be refreshed, and the
session ends.
"""

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib import auth
from django.http import HttpRequest
from django.http import HttpResponseBase
from django.utils import timezone

from accounts import oidc

AUTH_AT = "reellife_auth_at"
SEEN_AT = "reellife_seen_at"
CHECKED_AT = "reellife_checked_at"
# Record activity at most this often, to avoid a session write per request.
_SEEN_RESOLUTION = 60


def _now() -> int:
    return int(timezone.now().timestamp())


def mark_signed_in(sender: Any, request: HttpRequest, **kwargs: Any) -> None:
    """Stamps a new session with its sign-in time (user_logged_in)."""
    del sender, kwargs
    now = _now()
    request.session[AUTH_AT] = now
    request.session[SEEN_AT] = now
    request.session[CHECKED_AT] = now


class SessionTimeoutMiddleware:
    """Signs out sessions past the idle or absolute lifetime limit."""

    def __init__(
        self, get_response: Callable[[HttpRequest], HttpResponseBase]
    ) -> None:
        """Stores the next handler in the chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        """Expires the session if needed, then handles the request."""
        if request.user.is_authenticated:
            self._enforce(request)
        return self.get_response(request)

    @staticmethod
    def _enforce(request: HttpRequest) -> None:
        session = request.session
        now = _now()
        # Sessions always get both stamps at sign-in; a missing one (e.g.
        # from before this middleware existed) starts counting now.
        auth_at = session.setdefault(AUTH_AT, now)
        seen_at = session.setdefault(SEEN_AT, now)
        if (
            now - auth_at > settings.SESSION_ABSOLUTE_TIMEOUT
            or now - seen_at > settings.SESSION_IDLE_TIMEOUT
        ):
            # The user becomes anonymous, so LoginRequiredMiddleware sends
            # them to sign in again (usually silent at PocketID).
            auth.logout(request)
            return
        if now - seen_at >= _SEEN_RESOLUTION:
            session[SEEN_AT] = now
        # Sessions from before these checks are checked straight away.
        checked_at = session.setdefault(CHECKED_AT, 0)
        if now - checked_at >= settings.SESSION_CHECK_INTERVAL:
            if oidc.check_session(request):
                session[CHECKED_AT] = now
            else:
                auth.logout(request)
