"""Tests for the fake OIDC provider itself, so OIDC tests can trust it."""

import base64
import hashlib
from typing import Any
import urllib.parse

import jwt
import pytest

from tests.fakes import oidc_provider

ISSUER = "https://id.example.test"
USER = oidc_provider.DEV_USERS["composer"]
VERIFIER = "v" * 43


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _query(**overrides: str) -> dict[str, str]:
    query = {
        "client_id": "client",
        "response_type": "code",
        "scope": "openid email",
        "redirect_uri": "https://app.test/cb",
        "state": "st",
        "nonce": "n0",
        "code_challenge": _challenge(VERIFIER),
        "code_challenge_method": "S256",
    }
    query.update(overrides)
    return query


def _code(provider: oidc_provider.FakeProvider) -> str:
    location = provider.authorize(_query(), USER)
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    assert params["state"] == ["st"]
    return params["code"][0]


def _token_form(code: str, **overrides: str) -> dict[str, str]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "https://app.test/cb",
        "client_id": "client",
        "client_secret": "secret",
        "code_verifier": VERIFIER,
    }
    form.update(overrides)
    return form


@pytest.fixture(name="provider")
def _provider() -> oidc_provider.FakeProvider:
    return oidc_provider.FakeProvider(
        ISSUER, "client", "secret", clock=lambda: 1_000_000.0
    )


def _decode(provider: oidc_provider.FakeProvider, token: str) -> Any:
    header = jwt.get_unverified_header(token)
    (jwk,) = [k for k in provider.jwks()["keys"] if k["alg"] == header["alg"]]
    return jwt.decode(
        token,
        jwt.PyJWK(jwk),
        algorithms=[header["alg"]],
        audience="client",
        options={"verify_exp": False},
    )


@pytest.mark.parametrize("alg", ["RS256", "ES256"])
def test_code_flow_issues_verifiable_id_token(
    provider: oidc_provider.FakeProvider, alg: str
) -> None:
    provider.signing = alg
    status, body = provider.token(_token_form(_code(provider)))
    assert status == 200
    claims = _decode(provider, body["id_token"])
    assert claims["iss"] == ISSUER
    assert claims["sub"] == USER.sub
    assert claims["nonce"] == "n0"
    assert claims["groups"] == ["reellife-editors"]
    assert claims["exp"] == 1_000_300


def test_userinfo_returns_claims_for_access_token(
    provider: oidc_provider.FakeProvider,
) -> None:
    _, body = provider.token(_token_form(_code(provider)))
    status, info = provider.userinfo(f"Bearer {body['access_token']}")
    assert status == 200
    assert info["sub"] == USER.sub
    assert provider.userinfo("Bearer nope")[0] == 401


def test_code_is_single_use(provider: oidc_provider.FakeProvider) -> None:
    code = _code(provider)
    assert provider.token(_token_form(code))[0] == 200
    assert provider.token(_token_form(code))[0] == 400


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_verifier": "w" * 43},
        {"redirect_uri": "https://evil.test/cb"},
        {"grant_type": "password"},
    ],
)
def test_token_rejects_bad_grant(
    provider: oidc_provider.FakeProvider, overrides: dict[str, str]
) -> None:
    status, _ = provider.token(_token_form(_code(provider), **overrides))
    assert status == 400


def test_token_requires_client_secret(
    provider: oidc_provider.FakeProvider,
) -> None:
    form = _token_form(_code(provider), client_secret="wrong")
    assert provider.token(form)[0] == 401


def test_token_accepts_basic_client_auth(
    provider: oidc_provider.FakeProvider,
) -> None:
    form = _token_form(_code(provider))
    del form["client_secret"]
    basic = base64.b64encode(b"client:secret").decode()
    assert provider.token(form, f"Basic {basic}")[0] == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "other"},
        {"response_type": "token"},
        {"code_challenge_method": "plain"},
        {"scope": "email"},
    ],
)
def test_authorize_rejects_bad_requests(
    provider: oidc_provider.FakeProvider, overrides: dict[str, str]
) -> None:
    with pytest.raises(
        ValueError, match=r"client_id|response_type|PKCE|openid"
    ):
        provider.authorize(_query(**overrides), USER)


def test_discovery_can_omit_end_session(
    provider: oidc_provider.FakeProvider,
) -> None:
    assert "end_session_endpoint" in provider.discovery()
    provider.end_session = False
    assert "end_session_endpoint" not in provider.discovery()
