"""Client address handling that respects REELTIME_TRUST_PROXY."""

from django.conf import settings
from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str:
    """Returns the client's IP address.

    Behind a trusted proxy, that is the last X-Forwarded-For entry: the one
    the proxy appended. Earlier entries come from the client and can be
    forged, so they are never used.

    Args:
        request: The current request.

    Returns:
        The address, or an empty string if there is none.
    """
    return address(
        str(request.META.get("REMOTE_ADDR", "")),
        str(request.META.get("HTTP_X_FORWARDED_FOR", "")),
    )


def address(remote: str, forwarded: str) -> str:
    """The client's IP address from the connection and X-Forwarded-For.

    Shared by Django views (client_ip) and the ASGI router in front of
    the MCP server, so both count the same address.

    Args:
        remote: The connection's peer address.
        forwarded: The X-Forwarded-For header, or an empty string.

    Returns:
        The last forwarded entry behind a trusted proxy, else `remote`.
    """
    if settings.TRUST_PROXY:
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return remote


def ratelimit_key(group: str, request: HttpRequest) -> str:  # pylint: disable=unused-argument
    """Rate-limit key function: the client IP (django-ratelimit callable)."""
    return client_ip(request)
