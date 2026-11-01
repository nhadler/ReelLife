"""Error pages: a 404 inside the app when signed in, alone otherwise."""

from collections.abc import Callable

from django.test import Client
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
