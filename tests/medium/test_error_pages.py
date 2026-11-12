"""Error pages: a 404 inside the app when signed in, alone otherwise,
and a 400 for a host ReelLife doesn't serve."""

from collections.abc import Callable

from django.test import Client
from django.test import override_settings
import pytest

from accounts.models import Role

pytestmark = pytest.mark.django_db


def test_not_found_keeps_the_app_around_a_signed_in_user(
    role_client: Callable[[Role], Client],
) -> None:
    response = role_client(Role.VIEWER).get("/no/such/page/")
    assert response.status_code == 404
    html = response.content.decode()
    assert "<title>Page Not Found · ReelLife</title>" in html
    assert 'class="topbar' in html
    assert ">Go to Overview</a>" in html


def test_not_found_stands_alone_when_signed_out(client: Client) -> None:
    response = client.get("/no/such/page/")
    assert response.status_code == 404
    html = response.content.decode()
    assert "Page Not Found" in html
    assert 'class="topbar' not in html


@override_settings(DEBUG=False, ALLOWED_HOSTS=["reel.example.com"])
@pytest.mark.parametrize("path", ["/healthz", "/", "/auth/login/"])
def test_an_unknown_host_is_a_bad_request_not_an_error(path: str) -> None:
    # Turned away before sign-in runs: the 400 page renders with no user.
    client = Client(raise_request_exception=False)
    response = client.get(path, HTTP_HOST="192.168.1.189:8098")
    assert response.status_code == 400
    assert "<h1>Error 400</h1>" in response.content.decode()
    allowed = client.get("/healthz", HTTP_HOST="reel.example.com")
    assert allowed.status_code == 200
