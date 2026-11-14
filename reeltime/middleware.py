"""Response headers Django doesn't set itself."""

from collections.abc import Callable

from django.http import HttpRequest
from django.http import HttpResponseBase

from reeltime import net

# Deny every powerful browser feature; the app needs none of them.
PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()"
    for feature in (
        "accelerometer",
        "camera",
        "display-capture",
        "geolocation",
        "gyroscope",
        "magnetometer",
        "microphone",
        "midi",
        "payment",
        "usb",
    )
)


class PermissionsPolicyMiddleware:
    """Adds a Permissions-Policy header to every response."""

    def __init__(
        self, get_response: Callable[[HttpRequest], HttpResponseBase]
    ) -> None:
        """Stores the next handler in the chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        """Returns the response with the header added."""
        response = self.get_response(request)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return response


class ForwardedHeadersMiddleware:
    """Ignores X-Forwarded-* from a peer that isn't a trusted proxy.

    With REELTIME_TRUSTED_PROXIES set, a request that reaches ReelTime
    without going through one of them has these headers removed, so it
    can't claim to have come over HTTPS or from another address.
    """

    def __init__(
        self, get_response: Callable[[HttpRequest], HttpResponseBase]
    ) -> None:
        """Stores the next handler in the chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        """Drops the headers unless the peer is trusted, then goes on."""
        if not net.trusts(str(request.META.get("REMOTE_ADDR", ""))):
            for name in ("HTTP_X_FORWARDED_FOR", "HTTP_X_FORWARDED_PROTO"):
                request.META.pop(name, None)
        return self.get_response(request)
