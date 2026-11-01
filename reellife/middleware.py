"""Response headers Django doesn't set itself."""

from collections.abc import Callable

from django.http import HttpRequest
from django.http import HttpResponseBase

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
