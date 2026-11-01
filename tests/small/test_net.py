"""Tests for reellife.net.client_ip."""

from django.test import RequestFactory
from django.test import override_settings

from reellife import net


def test_uses_remote_addr_without_proxy() -> None:
    request = RequestFactory().get(
        "/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="6.6.6.6"
    )
    assert net.client_ip(request) == "10.0.0.1"


@override_settings(TRUST_PROXY=True)
def test_uses_entry_appended_by_trusted_proxy() -> None:
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9",
    )
    assert net.client_ip(request) == "203.0.113.9"


@override_settings(TRUST_PROXY=True)
def test_falls_back_to_remote_addr_without_header() -> None:
    request = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1")
    assert net.client_ip(request) == "127.0.0.1"


def test_ratelimit_key_is_client_ip() -> None:
    request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.2")
    assert net.ratelimit_key("login", request) == "10.0.0.2"
