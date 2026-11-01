"""Tests for accounts.authz.minimum_role and role_required."""

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.http import HttpResponse
from django.test import RequestFactory
import pytest

from accounts import authz
from accounts.models import Role
from accounts.models import User


def _view(request: HttpRequest) -> HttpResponse:
    del request
    return HttpResponse()


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_need_viewer(method: str) -> None:
    assert authz.minimum_role(_view, method) == Role.VIEWER


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_writes_need_editor(method: str) -> None:
    assert authz.minimum_role(_view, method) == Role.EDITOR


def test_declared_role_wins() -> None:
    def view(request: HttpRequest) -> HttpResponse:
        return _view(request)

    marked = authz.role_required(Role.ADMIN)(view)
    assert marked is view
    assert authz.minimum_role(marked, "GET") == Role.ADMIN
    assert authz.minimum_role(marked, "POST") == Role.ADMIN


def test_middleware_leaves_anonymous_users_to_login_middleware() -> None:
    request = RequestFactory().post("/")
    request.user = AnonymousUser()
    middleware = authz.RoleMiddleware(lambda r: HttpResponse())
    # No PermissionDenied: LoginRequiredMiddleware owns anonymous users.
    middleware.process_view(request, _view, (), {})


def test_middleware_refuses_an_inactive_user() -> None:
    request = RequestFactory().get("/")
    request.user = User(role=Role.ADMIN, is_active=False)
    middleware = authz.RoleMiddleware(lambda r: HttpResponse())
    with pytest.raises(PermissionDenied):
        middleware.process_view(request, _view, (), {})
