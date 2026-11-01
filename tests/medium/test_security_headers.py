"""Hardening headers stay on."""

from django.test import Client
import pytest

EXPECTED_CSP = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self'",
    "img-src": "'self' data:",
    "connect-src": "'self'",
    "frame-ancestors": "'none'",
    "form-action": "'self'",
    "base-uri": "'none'",
    "object-src": "'none'",
}


def _directives(header: str) -> dict[str, str]:
    pairs = (part.strip().partition(" ") for part in header.split(";"))
    return {name: value for name, _, value in pairs}


def test_csp_header_exact(client: Client) -> None:
    # django-csp doesn't keep directive order, so compare as a mapping.
    header = client.get("/healthz").headers["Content-Security-Policy"]
    assert _directives(header) == EXPECTED_CSP


def test_security_headers(client: Client) -> None:
    headers = client.get("/healthz").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "same-origin"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["X-Frame-Options"] == "DENY"
    assert "camera=()" in headers["Permissions-Policy"]
    assert "geolocation=()" in headers["Permissions-Policy"]


@pytest.mark.django_db
def test_error_pages_have_no_detail(client: Client) -> None:
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert b"Traceback" not in response.content
    assert b"Django" not in response.content
