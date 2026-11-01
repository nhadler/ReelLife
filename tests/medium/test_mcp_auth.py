"""MCP tokens: checked in ReelLife, role from PocketID."""

import asyncio
from collections.abc import Callable, Iterator
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
import jwt
import pytest
import requests

from accounts import models as account_models
from accounts import oidc
from mcp_server import auth

pytestmark = pytest.mark.django_db

ISSUER = "https://id.example.test"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
GROUPS = {"editor": ["reellife-editors"], "none": ["other"]}


class _Userinfo:
    """Stands in for PocketID's userinfo endpoint."""

    def __init__(self) -> None:
        self.calls = 0
        self.body: dict[str, Any] = {
            "sub": "user-1",
            "email": "casey@example.test",
            "groups": GROUPS["editor"],
        }
        self.fail = False

    def get(self, url: str, **kwargs: Any) -> Any:
        del url, kwargs
        self.calls += 1
        if self.fail:
            raise requests.ConnectionError("down")
        body = self.body

        class Response:  # pylint: disable=too-few-public-methods
            def raise_for_status(self) -> None:
                return None

            def json(self) -> Any:
                return body

        return Response()


@pytest.fixture(name="userinfo")
def fixture_userinfo(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Userinfo]:
    cache.clear()
    info = _Userinfo()
    monkeypatch.setattr(
        oidc,
        "provider_config",
        lambda: oidc.ProviderConfig(
            issuer=ISSUER,
            authorization_endpoint=f"{ISSUER}/authorize",
            token_endpoint=f"{ISSUER}/token",
            userinfo_endpoint=f"{ISSUER}/userinfo",
            jwks_uri=f"{ISSUER}/jwks",
        ),
    )
    monkeypatch.setattr(
        auth, "_signing_key", lambda token, uri: KEY.public_key()
    )
    monkeypatch.setattr("mcp_server.auth.requests.get", info.get)
    yield info
    cache.clear()


def _token(**claims: Any) -> str:
    now = int(time.time())
    body = {
        "iss": ISSUER,
        "aud": auth.resource_url(),
        "sub": "user-1",
        "iat": now,
        "exp": now + 600,
        "client_id": "https://claude.ai/oauth/mcp-client",
        "scope": "openid groups",
        **claims,
    }
    return jwt.encode(
        {k: v for k, v in body.items() if v is not None}, KEY, "RS256"
    )


def test_a_good_token_acts_for_the_user(userinfo: _Userinfo) -> None:
    token = auth.check(_token())
    assert token is not None
    assert token.resource == "http://testserver/mcp"
    assert token.client_id == "https://claude.ai/oauth/mcp-client"
    assert token.scopes == ["openid", "groups"]
    assert token.subject == "user-1"
    claims = token.claims or {}
    user = account_models.User.objects.get(oidc_sub="user-1")
    assert (claims["user_id"], claims["role"]) == (user.pk, "editor")
    # The role is cached: no second call to PocketID.
    assert auth.check(_token()) is not None
    assert userinfo.calls == 1


@pytest.mark.parametrize(
    "make",
    [
        lambda: _token(aud="https://other.example.test/api"),
        lambda: _token(iss="https://evil.example.test"),
        lambda: _token(exp=int(time.time()) - 3600),
        lambda: _token(sub=None),
        lambda: _token(sub=""),
        lambda: jwt.encode({"sub": "user-1"}, "s" * 32, "HS256"),
        lambda: "not a token",
    ],
)
def test_bad_tokens_are_refused(
    userinfo: _Userinfo, make: Callable[[], str]
) -> None:
    assert auth.check(make()) is None
    assert userinfo.calls == 0
    assert not account_models.AuditEvent.objects.exists()


def test_no_reellife_group_is_refused_and_audited(
    userinfo: _Userinfo,
) -> None:
    userinfo.body["groups"] = GROUPS["none"]
    assert auth.check(_token()) is None
    event = account_models.AuditEvent.objects.get()
    assert event.action == account_models.AuditAction.LOGIN_REFUSED
    assert event.detail["reason"] == "mcp_no_group"


def test_removal_seen_at_a_web_sign_in_reaches_a_cached_caller(
    userinfo: _Userinfo,
) -> None:
    assert auth.check(_token()) is not None
    # A web sign-in finds them in no group and clears the role...
    userinfo.body["groups"] = GROUPS["none"]
    with pytest.raises(oidc.LoginRefusedError):
        oidc.Backend()._sync_user(userinfo.body)  # pylint: disable=protected-access
    user = account_models.User.objects.get(oidc_sub="user-1")
    assert user.role == account_models.Role.NONE
    # ...so the cached MCP caller is dropped and PocketID asked again.
    assert auth.check(_token()) is None
    assert userinfo.calls == 2
    event = account_models.AuditEvent.objects.get(action="login_refused")
    assert event.detail["reason"] == "mcp_no_group"


def test_userinfo_for_someone_else_or_down(userinfo: _Userinfo) -> None:
    userinfo.body["sub"] = "user-2"
    assert auth.check(_token()) is None
    userinfo.fail = True
    assert auth.check(_token()) is None


@pytest.mark.django_db(transaction=True)
def test_the_async_hook(userinfo: _Userinfo) -> None:
    del userinfo
    verifier = auth.TokenVerifier()
    result = asyncio.run(verifier.verify_token(_token()))
    assert result is not None


def test_signing_keys_come_from_one_cached_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[str] = []

    class Client:  # pylint: disable=too-few-public-methods
        def __init__(self, uri: str, **kwargs: Any) -> None:
            assert not kwargs["cache_keys"]
            made.append(uri)

        def get_signing_key_from_jwt(self, token: str) -> Any:
            del token

            class Key:  # pylint: disable=too-few-public-methods
                key = "public"

            return Key()

    monkeypatch.setattr("mcp_server.auth.jwt.PyJWKClient", Client)
    monkeypatch.setattr(auth, "_jwks", {})
    uri = f"{ISSUER}/jwks"
    assert auth._signing_key("t", uri) == "public"  # pylint: disable=protected-access
    assert auth._signing_key("t", uri) == "public"  # pylint: disable=protected-access
    assert made == [uri]


def _jwk_set(*kids: str) -> dict[str, Any]:
    public = jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
    return {"keys": [{**public, "kid": kid, "alg": "RS256"} for kid in kids]}


def test_a_retired_key_stops_working_when_the_set_is_refreshed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = {"set": _jwk_set("old", "new")}
    monkeypatch.setattr(
        jwt.PyJWKClient, "fetch_data", lambda self: published["set"]
    )
    monkeypatch.setattr(auth, "_jwks", {})
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "jwt.jwk_set_cache.time.monotonic", lambda: clock["now"]
    )
    uri = f"{ISSUER}/jwks"
    old = jwt.encode({"sub": "x"}, KEY, "RS256", headers={"kid": "old"})
    assert auth._signing_key(old, uri) is not None  # pylint: disable=protected-access
    # PocketID retires the old key; the cached set expires an hour later.
    published["set"] = _jwk_set("new")
    clock["now"] += auth._JWKS_TTL + 1  # pylint: disable=protected-access
    with pytest.raises(jwt.PyJWKClientError):
        auth._signing_key(old, uri)  # pylint: disable=protected-access
