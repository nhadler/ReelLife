"""Tests for reeltime.net.client_ip."""

from django.test import RequestFactory
from django.test import override_settings

from reeltime import net


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


@override_settings(
    TRUST_PROXY=True, TRUSTED_PROXIES=("192.168.1.35", "10.9.0.0/24")
)
def test_only_listed_proxies_are_believed() -> None:
    forwarded = "6.6.6.6, 203.0.113.9"
    # The proxies themselves, by address or network.
    assert net.address("192.168.1.35", forwarded) == "203.0.113.9"
    assert net.address("10.9.0.77", forwarded) == "203.0.113.9"
    # Anything else speaks only for itself, whatever it sends.
    assert net.address("192.168.1.36", forwarded) == "192.168.1.36"
    assert net.address("2001:db8::1", forwarded) == "2001:db8::1"
    assert net.address("", forwarded) == ""
    assert not net.trusts("not an address")


@override_settings(TRUSTED_PROXIES=("192.168.1.35",))
def test_a_list_without_trust_believes_no_one() -> None:
    assert net.address("192.168.1.35", "203.0.113.9") == "192.168.1.35"
