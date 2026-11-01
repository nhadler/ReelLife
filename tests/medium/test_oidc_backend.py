"""Edge cases of accounts.oidc that the browser flow doesn't reach."""

from typing import Any
from unittest import mock

from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import SuspiciousOperation
from django.test import Client
from django.test import RequestFactory
import pytest
import responses

from accounts import models
from accounts import oidc as accounts_oidc
from reellife.settings import base
from tests.fakes import oidc_provider

pytestmark = pytest.mark.django_db

DISCOVERY = "https://id.example.test/.well-known/openid-configuration"


def _callback_request() -> Any:
    request = RequestFactory().get("/auth/oidc/callback/?code=c&state=s")
    request.session = SessionStore()
    return request


def _logout_request(user: models.User) -> Any:
    request = RequestFactory().post("/auth/logout/")
    request.user = user
    request.session = SessionStore()
    return request


@responses.activate
def test_discovery_must_be_an_object() -> None:
    responses.get(DISCOVERY, json=["not", "an", "object"])
    with pytest.raises(accounts_oidc.DiscoveryError, match="not an object"):
        accounts_oidc.provider_config()


@responses.activate
def test_discovery_must_have_every_endpoint() -> None:
    responses.get(DISCOVERY, json={"issuer": "https://id.example.test"})
    with pytest.raises(
        accounts_oidc.DiscoveryError, match="authorization_endpoint"
    ):
        accounts_oidc.provider_config()


def test_discovery_is_cached(oidc: oidc_provider.FakeProvider) -> None:
    first = accounts_oidc.provider_config()
    oidc.end_session = False
    assert accounts_oidc.provider_config() == first


def test_long_subjects_get_a_hashed_username() -> None:
    username = accounts_oidc._username_for("s" * 200)  # noqa: SLF001 pylint: disable=protected-access
    assert username.startswith("oidc-")
    assert len(username) == len("oidc-") + 64


def test_authenticate_ignores_non_callback_requests() -> None:
    backend = accounts_oidc.Backend()
    assert backend.authenticate(None) is None
    assert backend.authenticate(RequestFactory().get("/")) is None


@pytest.mark.usefixtures("oidc")
def test_missing_pkce_verifier_fails() -> None:
    backend = accounts_oidc.Backend()
    assert backend.authenticate(_callback_request(), nonce="n") is None
    event = models.AuditEvent.objects.get()
    assert event.action == "login_failed"
    assert event.detail == {"reason": "SuspiciousOperation"}


@pytest.mark.usefixtures("oidc")
def test_token_response_without_tokens_fails() -> None:
    backend = accounts_oidc.Backend()
    with mock.patch.object(backend, "get_token", return_value={}):
        user = backend.authenticate(
            _callback_request(), nonce="n", code_verifier="v" * 43
        )
    assert user is None
    assert models.AuditEvent.objects.get().action == "login_failed"


def test_malformed_id_token_is_rejected() -> None:
    with pytest.raises(SuspiciousOperation, match="malformed"):
        accounts_oidc.Backend().verify_token("not-a-jwt", nonce="n")


def test_userinfo_must_be_an_object() -> None:
    backend = accounts_oidc.Backend()
    with (
        mock.patch.object(backend, "get_userinfo", return_value=["x"]),
        pytest.raises(SuspiciousOperation, match="not an object"),
    ):
        backend._merged_claims("a", "i", {"sub": "s"})  # noqa: SLF001 pylint: disable=protected-access


@pytest.mark.parametrize("sub", ["", None, 42])
def test_sub_must_be_a_non_empty_string(sub: object) -> None:
    backend = accounts_oidc.Backend()
    with pytest.raises(SuspiciousOperation, match="sub claim missing"):
        backend._sync_user({"sub": sub})  # noqa: SLF001 pylint: disable=protected-access


def test_local_users_log_out_locally() -> None:
    user = models.User(username="local", role=models.Role.ADMIN)
    assert accounts_oidc.provider_logout_url(_logout_request(user)) is None


@pytest.mark.usefixtures("oidc")
def test_logout_survives_provider_outage(settings: Any) -> None:
    settings.OIDC_ISSUER = "https://down.example.test"
    user = models.User(username="u", oidc_sub="s")
    assert accounts_oidc.provider_logout_url(_logout_request(user)) is None


@pytest.mark.usefixtures("oidc")
def test_logout_without_stored_id_token_omits_hint() -> None:
    user = models.User(username="u", oidc_sub="s")
    url = accounts_oidc.provider_logout_url(_logout_request(user))
    assert url is not None
    assert "id_token_hint" not in url


def test_anonymous_logout_just_redirects(client: Client) -> None:
    response = client.post("/auth/logout/")
    assert response["Location"] == "/auth/login/"
    assert not models.AuditEvent.objects.exists()


def test_origin_drops_path() -> None:
    assert base.origin("https://id.example.com:8443/x/y") == (
        "https://id.example.com:8443"
    )
