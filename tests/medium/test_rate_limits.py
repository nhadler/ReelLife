"""Sign-in endpoints are rate limited per client IP."""

from django.test import Client
from django.test import override_settings
import pytest

from tests.fakes import oidc_provider


@pytest.mark.django_db
@pytest.mark.usefixtures("oidc")
@override_settings(RATELIMIT_LOGIN="3/m")
@pytest.mark.parametrize(
    "url", ["/auth/oidc/start/", "/auth/oidc/callback/?error=x"]
)
def test_sign_in_steps_are_limited(client: Client, url: str) -> None:
    for _ in range(3):
        assert client.get(url).status_code != 429
    response = client.get(url)
    assert response.status_code == 429
    assert response["Retry-After"] == "60"
    assert b"Too Many Attempts" in response.content


@pytest.mark.django_db
@override_settings(RATELIMIT_LOGIN="1/m")
def test_limits_are_per_client(
    client: Client, oidc: oidc_provider.FakeProvider
) -> None:
    del oidc
    url = "/auth/oidc/start/"
    assert client.get(url, REMOTE_ADDR="10.0.0.1").status_code == 302
    assert client.get(url, REMOTE_ADDR="10.0.0.1").status_code == 429
    assert client.get(url, REMOTE_ADDR="10.0.0.2").status_code == 302
