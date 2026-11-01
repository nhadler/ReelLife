"""Project-wide views."""

from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import render


def rate_limited(request: HttpRequest, exception: Exception) -> HttpResponse:
    """Shown when a client exceeds a rate limit (RATELIMIT_VIEW)."""
    del exception
    response = render(request, "429.html", status=429)
    response["Retry-After"] = "60"
    return response
