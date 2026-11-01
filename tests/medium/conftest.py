"""Fixtures for medium tests: a fake OIDC provider and signed-in clients."""

from collections.abc import Callable, Iterator
import json
from typing import Any
import urllib.parse

from django.conf import settings
from django.test import Client
from django.test import override_settings
import pytest
import requests
import responses

from accounts import models
from tests.fakes import oidc_provider

ResponseTuple = tuple[int, dict[str, str], str]


def _json(result: tuple[int, dict[str, Any]]) -> ResponseTuple:
    status, body = result
    return status, {"Content-Type": "application/json"}, json.dumps(body)


@pytest.fixture(name="oidc")
def fixture_oidc() -> Iterator[oidc_provider.FakeProvider]:
    """A fake provider wired into `requests`; no real network is used."""
    provider = oidc_provider.FakeProvider(
        settings.OIDC_ISSUER,
        settings.OIDC_RP_CLIENT_ID,
        settings.OIDC_RP_CLIENT_SECRET,
    )

    def token(request: requests.PreparedRequest) -> ResponseTuple:
        body = request.body or ""
        if isinstance(body, bytes):
            body = body.decode()
        form = dict(urllib.parse.parse_qsl(body))
        return _json(provider.token(form, request.headers.get("Authorization")))

    def userinfo(request: requests.PreparedRequest) -> ResponseTuple:
        return _json(provider.userinfo(request.headers.get("Authorization")))

    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add_callback(
            responses.GET,
            provider.url("/.well-known/openid-configuration"),
            callback=lambda _: _json((200, provider.discovery())),
        )
        mock.add_callback(
            responses.GET,
            provider.url("/.well-known/jwks.json"),
            callback=lambda _: _json((200, provider.jwks())),
        )
        mock.add_callback(
            responses.POST, provider.url("/api/oidc/token"), callback=token
        )
        mock.add_callback(
            responses.GET, provider.url("/api/oidc/userinfo"), callback=userinfo
        )
        for user in oidc_provider.DEV_USERS.values():
            if user.colour:
                mock.add(
                    responses.GET,
                    provider.picture_url(user),
                    body=oidc_provider.picture_png(user),
                    content_type="image/png",
                )
        yield provider


SignIn = Callable[..., Any]


@pytest.fixture(name="sign_in")
def fixture_sign_in(
    oidc: oidc_provider.FakeProvider,
) -> SignIn:
    """Runs the browser side of the OIDC flow for a test client.

    Returns a function (client, user, next=None) -> callback response.
    """

    def run(
        client: Client,
        user: oidc_provider.FakeUser,
        next_url: str | None = None,
    ) -> Any:
        start_url = "/auth/oidc/start/"
        if next_url is not None:
            start_url += "?" + urllib.parse.urlencode({"next": next_url})
        start = client.get(start_url)
        assert start.status_code == 302, start.content
        query = dict(
            urllib.parse.parse_qsl(
                urllib.parse.urlsplit(start["Location"]).query
            )
        )
        callback = urllib.parse.urlsplit(oidc.authorize(query, user))
        return client.get(f"{callback.path}?{callback.query}")

    return run


def _client_for(role: models.Role) -> Client:
    user = models.User.objects.create(
        username=f"test-{role.value}",
        oidc_sub=f"sub-{role.value}",
        role=role,
        is_staff=role == models.Role.ADMIN,
        is_superuser=role == models.Role.ADMIN,
    )
    client = Client()
    client.force_login(user, backend="accounts.oidc.Backend")
    return client


@pytest.fixture(name="role_client")
def fixture_role_client(db: None) -> Callable[[models.Role], Client]:
    """Returns a factory of clients signed in with a given role."""
    del db
    return _client_for


@pytest.fixture(autouse=True)
def _no_real_https_redirects() -> Iterator[None]:
    with override_settings(SECURE_SSL_REDIRECT=False):
        yield
