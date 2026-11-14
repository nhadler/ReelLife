"""Client address handling that respects REELTIME_TRUST_PROXY."""

import functools
import ipaddress

from django.conf import settings
from django.http import HttpRequest

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@functools.lru_cache(maxsize=8)
def _networks(entries: tuple[str, ...]) -> tuple[Network, ...]:
    return tuple(ipaddress.ip_network(entry, strict=False) for entry in entries)


def trusts(remote: str) -> bool:
    """Whether what a connection's peer says about the client is believed.

    With REELTIME_TRUST_PROXY on, every peer is, unless
    REELTIME_TRUSTED_PROXIES lists the proxies: then only they are, and a
    client that reaches ReelTime some other way can't pass itself off as
    someone else or as arriving over HTTPS.

    Args:
        remote: The connection's peer address.
    """
    if not settings.TRUST_PROXY:
        return False
    listed = _networks(tuple(settings.TRUSTED_PROXIES))
    if not listed:
        return True
    try:
        peer = ipaddress.ip_address(remote)
    except ValueError:
        return False
    return any(peer in network for network in listed)


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
        The last forwarded entry when the peer is a trusted proxy, else
        `remote`.
    """
    if trusts(remote):
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return remote


def ratelimit_key(group: str, request: HttpRequest) -> str:  # pylint: disable=unused-argument
    """Rate-limit key function: the client IP (django-ratelimit callable)."""
    return client_ip(request)
