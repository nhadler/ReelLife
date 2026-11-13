"""Container health probe: `python -m reeltime.healthcheck`.

The slim image has no curl, so Docker's HEALTHCHECK runs this. It asks
gunicorn for /healthz on loopback, using a configured host name so
Django's ALLOWED_HOSTS accepts the request.
"""

import http.client
import os
import sys

_PORT = 8000


def probe_host(allowed_hosts: str) -> str:
    """Returns a Host header ALLOWED_HOSTS accepts.

    Args:
        allowed_hosts: The REELTIME_ALLOWED_HOSTS value.

    Returns:
        The first entry, without a leading '.' wildcard dot; 'localhost'
        if there is no usable entry.
    """
    for entry in allowed_hosts.split(","):
        host = entry.strip().lstrip(".")
        if host and host != "*":
            return host
    return "localhost"


def check(host: str, port: int = _PORT) -> bool:
    """Returns whether /healthz answers 200 'ok'."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", "/healthz", headers={"Host": host})
        response = connection.getresponse()
        return response.status == 200 and response.read() == b"ok"
    except OSError:
        return False
    finally:
        connection.close()


def main() -> int:
    """Exits 0 when healthy, 1 otherwise."""
    host = probe_host(os.environ.get("REELTIME_ALLOWED_HOSTS", ""))
    return 0 if check(host) else 1


if __name__ == "__main__":
    sys.exit(main())
