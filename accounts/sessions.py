"""Session lifetimes: 8 hours idle, 7 days absolute.

Django's own expiry slides forward whenever the session is saved, so the
absolute limit is enforced here from the sign-in time. Every
SESSION_CHECK_INTERVAL an OIDC session also asks PocketID whether its
user may still use ReelTime (accounts.oidc.check_session): removed from
the ReelTime groups, disabled, or tokens that can't be refreshed, and the
session ends.
"""

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib import auth
from django.contrib.auth import middleware as auth_middleware
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseBase
from django.http import JsonResponse
from django.utils import timezone

from accounts import oidc

AUTH_AT = "reeltime_auth_at"
SEEN_AT = "reeltime_seen_at"
CHECKED_AT = "reeltime_checked_at"
# Record activity at most this often, to avoid a session write per request.
_SEEN_RESOLUTION = 60
SIGNED_OUT = "You've been signed out. Reload the page to sign in again."


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


class LoginRequiredMiddleware(auth_middleware.LoginRequiredMiddleware):
    """Django's, except where a redirect to the sign-in page can't work.

    A page left open past the session's end still makes requests. The
    grid's JSON API (/api/) gets a 401 with a message instead of the
    sign-in page's HTML, so the grid says what happened and an edit isn't
    silently lost. An htmx request (a dialog) reloads the page, which then
    goes to sign in, instead of showing the sign-in page in the dialog.
    """

    # Django's stub promises a redirect; process_view only returns this
    # as the response, so any response works.
    def handle_no_permission(  # type: ignore[override]
        self, request: HttpRequest, view_func: Callable[..., Any]
    ) -> HttpResponseBase:
        """See the class docstring; other requests go to sign in."""
        if request.path_info.startswith("/api/"):
            return JsonResponse(
                {"error": SIGNED_OUT, "signed_out": True}, status=401
            )
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=401)
            response["HX-Refresh"] = "true"
            return response
        return super().handle_no_permission(request, view_func)
