"""Users deactivated in ReelLife lose access everywhere at once."""

from collections.abc import Callable, Iterator
from typing import Any

from django.core.cache import cache
from django.test import Client
import jwt
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
import pytest

from accounts import models
from mcp_server import auth
from mcp_server import common
from tests.fakes import oidc_provider
from tests.medium.conftest import SignIn

pytestmark = pytest.mark.django_db

COMPOSER = oidc_provider.DEV_USERS["composer"]


def _deactivate(**lookup: Any) -> models.User:
    user = models.User.objects.get(**lookup)
    user.is_active = False
    user.save()
    return user


@pytest.fixture(name="mcp_keys")
def fixture_mcp_keys(
    monkeypatch: pytest.MonkeyPatch, oidc: oidc_provider.FakeProvider
) -> Iterator[None]:
    """Verifies MCP tokens with the fake provider's published keys."""
    keys = {
        key["kid"]: jwt.PyJWK.from_dict(key).key for key in oidc.jwks()["keys"]
    }

    def lookup(token: str, uri: str) -> Any:
        del uri
        return keys[jwt.get_unverified_header(token)["kid"]]

    monkeypatch.setattr(auth, "_signing_key", lookup)
    cache.clear()
    yield
    cache.clear()


def test_an_open_session_stops_working(
    role_client: Callable[[models.Role], Client],
) -> None:
    client = role_client(models.Role.EDITOR)
    assert client.get("/").status_code == 200
    _deactivate(username="test-editor")
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"].startswith("/auth/login/")
    assert client.post("/projects/new/", {}).status_code == 302


def test_signing_in_again_is_refused_and_audited(
    client: Client, sign_in: SignIn
) -> None:
    sign_in(client, COMPOSER)
    client.logout()
    _deactivate(oidc_sub=COMPOSER.sub)
    refused = sign_in(client, COMPOSER)
    assert refused.status_code == 403
    assert "account is turned off" in refused.content.decode()
    assert client.get("/").status_code == 302
    event = models.AuditEvent.objects.get(action="login_refused")
    assert event.detail == {"reason": "inactive"}
    assert models.User.objects.get(oidc_sub=COMPOSER.sub).role == "editor"


def test_mcp_tools_refuse_an_inactive_user(
    role_client: Callable[[models.Role], Client],
) -> None:
    role_client(models.Role.EDITOR)
    user = _deactivate(username="test-editor")
    token = AccessToken(
        token="t", client_id="client", scopes=[], claims={"user_id": user.pk}
    )
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        with pytest.raises(ToolError, match="viewer role"):
            common.user(models.Role.VIEWER)
    finally:
        auth_context_var.reset(reset)


@pytest.mark.usefixtures("mcp_keys")
def test_mcp_drops_a_cached_caller_who_was_deactivated(
    oidc: oidc_provider.FakeProvider,
) -> None:
    token = oidc.resource_token(COMPOSER, auth.resource_url())
    allowed = auth.check(token)
    assert allowed is not None
    assert (allowed.claims or {})["role"] == "editor"
    _deactivate(oidc_sub=COMPOSER.sub)
    assert auth.check(token) is None
    event = models.AuditEvent.objects.get(action="login_refused")
    assert event.detail == {"reason": "mcp_inactive"}
    # Turned back on, the user gets in again once PocketID agrees.
    models.User.objects.filter(oidc_sub=COMPOSER.sub).update(is_active=True)
    assert auth.check(token) is not None
