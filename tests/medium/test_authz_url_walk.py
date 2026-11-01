"""Walks every URL pattern and checks who may reach it.

New URLs are covered automatically: a view is protected unless it is
explicitly public, and the public set below must be updated on purpose.
"""

from collections.abc import Callable, Iterator
import dataclasses
import re
from typing import Any

from django.test import Client
from django.urls import URLPattern
from django.urls import URLResolver
from django.urls import get_resolver
from django.urls import resolve
from django.urls import reverse
from django.urls.resolvers import RoutePattern
import pytest

from accounts.models import Role

PUBLIC = {
    "healthz",
    "readyz",
    "accounts:login",
    "accounts:oidc_start",
    "accounts:oidc_callback",
    "accounts:logout",
    "accounts:local_login",
    "admin:login",
}

_ROUTE_PARAM = re.compile(r"<(?:(?P<converter>\w+):)?\w+>")
# Named or unnamed regex groups, e.g. (?P<app_label>a|b) or ([^/]+).
_REGEX_GROUP = re.compile(r"\((?:\?P<\w+>)?(?P<body>[^)]*)\)")
_SAMPLE_BY_CONVERTER = {
    "int": "1",
    "uuid": "00000000-0000-0000-0000-000000000001",
}


@dataclasses.dataclass(frozen=True)
class Route:
    name: str
    url: str
    callback: Callable[..., Any]

    @property
    def public(self) -> bool:
        return not getattr(self.callback, "login_required", True)


def _sample(pattern: URLPattern | URLResolver) -> str:
    """Builds a concrete path fragment that the pattern matches."""
    text = str(pattern.pattern)
    if isinstance(pattern.pattern, RoutePattern):
        return _ROUTE_PARAM.sub(
            lambda m: _SAMPLE_BY_CONVERTER.get(m["converter"] or "", "x"), text
        )

    def group(match: re.Match[str]) -> str:
        first = match["body"].split("|")[0]
        return first if re.fullmatch(r"[\w-]+", first) else "x"

    return _REGEX_GROUP.sub(group, text).lstrip("^").rstrip("$")


def _routes(
    resolver: URLResolver, prefix: str = "/", namespace: str = ""
) -> Iterator[Route]:
    for entry in resolver.url_patterns:
        path = prefix + _sample(entry)
        if isinstance(entry, URLResolver):
            inner = namespace
            if entry.namespace:
                inner = f"{namespace}{entry.namespace}:"
            yield from _routes(entry, path, inner)
            continue
        name = namespace + (entry.name or f"<{entry.pattern}>")
        assert resolve(path).func is entry.callback, f"{name}: {path}"
        yield Route(name, path, entry.callback)


ROUTES = list(_routes(get_resolver()))
PROTECTED = [route for route in ROUTES if not route.public]
ADMIN = [route for route in PROTECTED if route.name.startswith("admin:")]


def _ids(route: Route) -> str:
    return route.name


def test_public_urls_are_exactly_the_expected_ones() -> None:
    assert {route.name for route in ROUTES if route.public} == PUBLIC


def test_routes_were_found() -> None:
    assert len(PROTECTED) > 10
    assert ADMIN


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("route", PROTECTED, ids=_ids)
def test_anonymous_is_sent_to_login(
    client: Client, route: Route, method: str
) -> None:
    # Admin pages hop through /admin/login/, which ReelLife redirects on.
    response = getattr(client, method)(route.url, follow=True)
    assert response.redirect_chain, route.url
    final, _ = response.redirect_chain[-1]
    assert final.startswith("/auth/login/?next="), route.url
    assert response.status_code == 200, route.url


@pytest.mark.parametrize("route", PROTECTED, ids=_ids)
def test_user_without_role_is_refused(
    role_client: Callable[[Role], Client], route: Route
) -> None:
    assert role_client(Role.NONE).get(route.url).status_code == 403


@pytest.mark.parametrize("route", PROTECTED, ids=_ids)
def test_viewer_cannot_write(
    role_client: Callable[[Role], Client], route: Route
) -> None:
    assert role_client(Role.VIEWER).post(route.url).status_code == 403


@pytest.mark.parametrize("route", ADMIN, ids=_ids)
def test_editor_cannot_use_admin(
    role_client: Callable[[Role], Client], route: Route
) -> None:
    response = role_client(Role.EDITOR).get(route.url, follow=True)
    assert response.status_code == 403, route.url


def test_admin_can_use_admin(role_client: Callable[[Role], Client]) -> None:
    client = role_client(Role.ADMIN)
    assert client.get(reverse("admin:index")).status_code == 200
    users = reverse("admin:accounts_user_changelist")
    assert client.get(users).status_code == 200


def test_editor_and_viewer_can_read_the_dashboard(
    role_client: Callable[[Role], Client],
) -> None:
    for role in (Role.VIEWER, Role.EDITOR):
        assert role_client(role).get("/").status_code == 200


def test_admin_login_sends_anonymous_users_to_pocketid(client: Client) -> None:
    response = client.get(reverse("admin:login"))
    assert response["Location"].startswith("/auth/login/?next=")


def test_admin_logout_uses_reellife_logout(
    role_client: Callable[[Role], Client],
) -> None:
    client = role_client(Role.ADMIN)
    response = client.post(reverse("admin:logout"))
    assert response["Location"] == "/auth/login/"
    assert client.get("/").status_code == 302
