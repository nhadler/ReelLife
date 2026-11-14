"""REELTIME_TRUSTED_PROXIES: only the listed proxies' headers count."""

from django.test import Client
from django.test import override_settings
import pytest

from tests.fakes import oidc_provider

pytestmark = pytest.mark.django_db

PROXY = "192.168.1.35"
ELSEWHERE = "192.168.1.36"


@override_settings(
    TRUST_PROXY=True, TRUSTED_PROXIES=(PROXY,), RATELIMIT_LOGIN="1/m"
)
def test_rate_limits_count_the_address_a_trusted_proxy_gives(
    client: Client, oidc: oidc_provider.FakeProvider
) -> None:
    del oidc
    url = "/auth/oidc/start/"

    def get(peer: str, forwarded: str) -> int:
        response = client.get(
            url, REMOTE_ADDR=peer, HTTP_X_FORWARDED_FOR=forwarded
        )
        return int(response.status_code)

    # Through the proxy, each visitor is counted on their own.
    assert get(PROXY, "203.0.113.1") == 302
    assert get(PROXY, "203.0.113.2") == 302
    assert get(PROXY, "203.0.113.1") == 429
    # Reaching ReelTime another way, a client is itself whatever it says.
    assert get(ELSEWHERE, "203.0.113.3") == 302
    assert get(ELSEWHERE, "203.0.113.4") == 429


@override_settings(
    TRUST_PROXY=True,
    TRUSTED_PROXIES=(PROXY,),
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
)
def test_only_a_trusted_proxy_can_say_it_was_https(client: Client) -> None:
    through = client.get(
        "/auth/login/", REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_PROTO="https"
    )
    assert through.status_code == 200
    direct = client.get(
        "/auth/login/", REMOTE_ADDR=ELSEWHERE, HTTP_X_FORWARDED_PROTO="https"
    )
    assert direct.status_code == 301
    assert direct["Location"].startswith("https://")


@override_settings(TRUST_PROXY=True, RATELIMIT_LOGIN="1/m")
def test_without_a_list_any_peer_is_the_proxy(
    client: Client, oidc: oidc_provider.FakeProvider
) -> None:
    del oidc
    url = "/auth/oidc/start/"
    for visitor in ("203.0.113.1", "203.0.113.2"):
        response = client.get(
            url, REMOTE_ADDR=ELSEWHERE, HTTP_X_FORWARDED_FOR=visitor
        )
        assert response.status_code == 302
