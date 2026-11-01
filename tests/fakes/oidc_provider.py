"""A fake OpenID Connect provider for tests and local development.

Implements the parts of the authorization code flow ReelLife uses:
discovery, JWKS, authorize (PKCE required), token, userinfo,
end-session and profile pictures. Tests drive it in-process through the
`responses` library;
`python -m tests.fakes.oidc_provider` serves it over HTTP for `just dev`,
with a page to pick which fake user signs in.

Tests can make it misbehave to check that ReelLife refuses bad tokens:
see `id_token_overrides`, `userinfo_overrides` and `signing`.
"""

import base64
from collections.abc import Callable, Iterable, Mapping
import dataclasses
import hashlib
import html
import io
import json
import secrets
import time
from typing import Any
import urllib.parse
from wsgiref import simple_server
from wsgiref.types import StartResponse
from wsgiref.types import WSGIEnvironment

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from jwt import algorithms
from PIL import Image
from PIL import ImageDraw

KID_RSA = "fake-rsa"
KID_EC = "fake-ec"

JsonResponse = tuple[int, dict[str, Any]]


@dataclasses.dataclass(frozen=True)
class FakeUser:
    """Someone who can sign in to the fake provider."""

    sub: str
    email: str
    name: str
    groups: tuple[str, ...]
    # The picture's colour; None for a user who never uploaded one.
    colour: str | None = None


# Users offered by the dev sign-in page, keyed by a short handle.
DEV_USERS = {
    "composer": FakeUser(
        "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
        "composer@example.test",
        "Casey Composer",
        ("reellife-editors",),
        "#3b6ea8",
    ),
    "nick": FakeUser(
        "6fa459ea-ee8a-3ca4-894e-db77e160355e",
        "nick@example.test",
        "Nick Admin",
        ("reellife-admins",),
        "#a8563b",
    ),
    "viewer": FakeUser(
        "886313e1-3b8a-5372-9b90-0c9aee199e5d",
        "viewer@example.test",
        "Vic Viewer",
        ("reellife-viewers",),
    ),
    "outsider": FakeUser(
        "a3bb189e-8bf9-3888-9912-ace4e6543002",
        "outsider@example.test",
        "Olly Outsider",
        ("some-other-app",),
    ),
}


@dataclasses.dataclass
class _Grant:
    user: FakeUser
    client_id: str
    redirect_uri: str
    nonce: str | None
    code_challenge: str
    offline: bool = False  # asked for offline_access


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _public_jwk(key: Any, kid: str, alg: str) -> dict[str, Any]:
    if alg.startswith("RS"):
        jwk = json.loads(algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    else:
        jwk = json.loads(algorithms.ECAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid=kid, alg=alg, use="sig")
    assert isinstance(jwk, dict)
    return jwk


class FakeProvider:  # pylint: disable=too-many-instance-attributes
    """An in-memory OIDC provider.

    Attributes:
        issuer: The issuer URL, also the base of every endpoint.
        client_id: The only client it accepts.
        client_secret: That client's secret.
        signing: How to sign ID tokens: 'RS256' and 'ES256' are valid;
            'none', 'HS256' (keyed with the client secret) and 'wrong-key'
            (an unpublished key) are attacks ReelLife must refuse.
        id_token_overrides: Claims to replace in the next ID tokens.
        userinfo_overrides: Claims to replace in userinfo responses.
        end_session: Whether discovery advertises end_session_endpoint.
        groups_in: Where the groups claim goes: 'id_token', 'userinfo' or
            'both'. Providers differ, and ReelLife reads either.
        issue_refresh_tokens: Whether code exchanges also return a
            refresh token (only when the client asked for offline_access,
            as the OIDC spec has it).
        rotate_refresh_tokens: Whether using a refresh token replaces it
            with a new one, or keeps it valid.
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Creates a provider with fresh signing keys."""
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self._clock = clock
        self._rsa_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self._ec_key = ec.generate_private_key(ec.SECP256R1())
        self._grants: dict[str, _Grant] = {}
        self._access_tokens: dict[str, FakeUser] = {}
        self.signing = "RS256"
        self.id_token_overrides: dict[str, Any] = {}
        self.userinfo_overrides: dict[str, Any] = {}
        self.end_session = True
        self.groups_in = "both"
        self.issue_refresh_tokens = False
        self.rotate_refresh_tokens = True
        self._refresh_tokens: dict[str, FakeUser] = {}

    def url(self, path: str) -> str:
        """Returns the absolute URL of an endpoint path."""
        return f"{self.issuer}{path}"

    def discovery(self) -> dict[str, Any]:
        """Returns the /.well-known/openid-configuration document."""
        doc: dict[str, Any] = {
            "issuer": self.issuer,
            "authorization_endpoint": self.url("/authorize"),
            "token_endpoint": self.url("/api/oidc/token"),
            "userinfo_endpoint": self.url("/api/oidc/userinfo"),
            "jwks_uri": self.url("/.well-known/jwks.json"),
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256", "ES256"],
            "code_challenge_methods_supported": ["S256"],
        }
        if self.end_session:
            doc["end_session_endpoint"] = self.url("/api/oidc/end-session")
        return doc

    def jwks(self) -> dict[str, Any]:
        """Returns the public signing keys."""
        return {
            "keys": [
                _public_jwk(self._rsa_key, KID_RSA, "RS256"),
                _public_jwk(self._ec_key, KID_EC, "ES256"),
            ]
        }

    def authorize(self, query: Mapping[str, str], user: FakeUser) -> str:
        """Approves an authorization request as `user`.

        Args:
            query: The authorization request's query parameters.
            user: Who signs in.

        Returns:
            The URL to redirect the browser to: the client's redirect_uri
            with a code and the state.

        Raises:
            ValueError: If the request is not a valid PKCE code request for
                this client.
        """
        if query.get("client_id") != self.client_id:
            raise ValueError("unknown client_id")
        if query.get("response_type") != "code":
            raise ValueError("only response_type=code is supported")
        if query.get("code_challenge_method") != "S256":
            raise ValueError("PKCE with S256 is required")
        if "openid" not in query.get("scope", "").split():
            raise ValueError("scope must include openid")
        code = secrets.token_urlsafe(24)
        self._grants[code] = _Grant(
            user=user,
            client_id=query["client_id"],
            redirect_uri=query["redirect_uri"],
            nonce=query.get("nonce"),
            code_challenge=query["code_challenge"],
            offline="offline_access" in query.get("scope", "").split(),
        )
        params = urllib.parse.urlencode(
            {"code": code, "state": query.get("state", "")}
        )
        return f"{query['redirect_uri']}?{params}"

    def _client_authenticated(
        self, form: Mapping[str, str], authorization: str | None
    ) -> bool:
        if authorization and authorization.startswith("Basic "):
            decoded = base64.b64decode(authorization[6:]).decode()
            client_id, _, secret = decoded.partition(":")
        else:
            client_id = form.get("client_id", "")
            secret = form.get("client_secret", "")
        return client_id == self.client_id and secrets.compare_digest(
            secret, self.client_secret
        )

    def token(
        self, form: Mapping[str, str], authorization: str | None = None
    ) -> JsonResponse:
        """Exchanges an authorization code for tokens.

        Args:
            form: The token request's form fields.
            authorization: The Authorization header, if any.

        Returns:
            (HTTP status, JSON body).
        """
        if not self._client_authenticated(form, authorization):
            return 401, {"error": "invalid_client"}
        if form.get("grant_type") == "refresh_token":
            refresh_token = form.get("refresh_token", "")
            user = self._refresh_tokens.get(refresh_token)
            if user is None:
                return 400, {"error": "invalid_grant"}
            if not self.rotate_refresh_tokens:
                return 200, self._tokens_for(user, refresh=False)
            del self._refresh_tokens[refresh_token]
            return 200, self._tokens_for(user)
        grant = self._grants.pop(form.get("code", ""), None)
        if grant is None or form.get("grant_type") != "authorization_code":
            return 400, {"error": "invalid_grant"}
        if form.get("redirect_uri") != grant.redirect_uri:
            return 400, {"error": "invalid_grant"}
        verifier = form.get("code_verifier", "")
        if _s256(verifier) != grant.code_challenge:
            return 400, {"error": "invalid_grant"}
        return 200, {
            **self._tokens_for(grant.user, refresh=grant.offline),
            "id_token": self._id_token(grant),
        }

    def _tokens_for(
        self, user: FakeUser, *, refresh: bool = True
    ) -> dict[str, Any]:
        access_token = secrets.token_urlsafe(24)
        self._access_tokens[access_token] = user
        tokens: dict[str, Any] = {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        if refresh and self.issue_refresh_tokens:
            refresh_token = secrets.token_urlsafe(24)
            self._refresh_tokens[refresh_token] = user
            tokens["refresh_token"] = refresh_token
        return tokens

    def expire_access_tokens(self) -> None:
        """Makes every access token issued so far invalid, as time does."""
        self._access_tokens.clear()

    def revoke(self, user: FakeUser) -> None:
        """Invalidates all of a user's tokens, as disabling them would."""
        for tokens in (self._access_tokens, self._refresh_tokens):
            for token in [t for t, u in tokens.items() if u == user]:
                del tokens[token]

    def _id_token(self, grant: _Grant) -> str:
        now = int(self._clock())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": grant.user.sub,
            "aud": grant.client_id,
            "iat": now,
            "exp": now + 300,
            "email": grant.user.email,
            "name": grant.user.name,
        }
        if grant.nonce is not None:
            claims["nonce"] = grant.nonce
        if self.groups_in in ("id_token", "both"):
            claims["groups"] = list(grant.user.groups)
        claims.update(self.id_token_overrides)
        return self._sign(claims)

    def _sign(self, claims: dict[str, Any]) -> str:
        if self.signing == "RS256":
            return jwt.encode(
                claims, self._rsa_key, "RS256", headers={"kid": KID_RSA}
            )
        if self.signing == "ES256":
            return jwt.encode(
                claims, self._ec_key, "ES256", headers={"kid": KID_EC}
            )
        if self.signing == "HS256":
            return jwt.encode(
                claims, self.client_secret, "HS256", headers={"kid": KID_RSA}
            )
        if self.signing == "wrong-key":
            other = rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            )
            return jwt.encode(claims, other, "RS256", headers={"kid": KID_RSA})
        if self.signing == "none":
            return jwt.encode(claims, "", "none", headers={"kid": KID_RSA})
        raise ValueError(f"unknown signing mode {self.signing!r}")

    def resource_token(
        self,
        user: FakeUser,
        audience: str,
        client_id: str = "https://claude.ai/oauth/mcp-client",
    ) -> str:
        """A signed access token for a resource (RFC 8707), as PocketID
        issues for an API registered under it; userinfo honours it too."""
        now = int(self._clock())
        token = self._sign(
            {
                "iss": self.issuer,
                "aud": audience,
                "sub": user.sub,
                "client_id": client_id,
                "scope": "openid profile email groups",
                "iat": now,
                "exp": now + 600,
            }
        )
        self._access_tokens[token] = user
        return token

    def userinfo(self, authorization: str | None) -> JsonResponse:
        """Returns the claims for a bearer access token."""
        token = (authorization or "").removeprefix("Bearer ")
        user = self._access_tokens.get(token)
        if user is None:
            return 401, {"error": "invalid_token"}
        claims: dict[str, Any] = {
            "sub": user.sub,
            "email": user.email,
            "email_verified": True,
            "name": user.name,
            "given_name": user.name.split()[0],
            "family_name": user.name.split()[-1],
        }
        if user.colour:
            claims["picture"] = self.picture_url(user)
        if self.groups_in in ("userinfo", "both"):
            claims["groups"] = list(user.groups)
        claims.update(self.userinfo_overrides)
        return 200, claims

    def picture_url(self, user: FakeUser) -> str:
        """Where the user's picture is, as PocketID serves them."""
        return f"{self.issuer}/api/users/{user.sub}/profile-picture.png"


def picture_png(user: FakeUser) -> bytes:
    """A stand-in photo: the user's colour with a lighter head and shoulders."""
    image = Image.new("RGB", (240, 240), user.colour or "#777777")
    draw = ImageDraw.Draw(image)
    draw.ellipse((80, 40, 160, 120), fill="#f2e6d8")
    draw.ellipse((40, 140, 200, 300), fill="#f2e6d8")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


# Serving over HTTP for `just dev`.


def _respond_json(
    start_response: StartResponse, status: int, body: dict[str, Any]
) -> Iterable[bytes]:
    payload = json.dumps(body).encode()
    start_response(
        f"{status} {'OK' if status == 200 else 'Error'}",
        [("Content-Type", "application/json")],
    )
    return [payload]


def _redirect(start_response: StartResponse, url: str) -> Iterable[bytes]:
    start_response("302 Found", [("Location", url)])
    return [b""]


def _picker(query: Mapping[str, str]) -> bytes:
    rows = []
    for handle, user in DEV_USERS.items():
        link = urllib.parse.urlencode({**query, "as": handle})
        groups = ", ".join(user.groups)
        rows.append(
            f'<li><a href="/authorize?{html.escape(link)}">'
            f"{html.escape(user.name)}</a> <small>({groups})</small></li>"
        )
    return (
        "<!doctype html><title>Fake sign-in</title>"
        "<h1>Fake OIDC provider</h1><p>Sign in to ReelLife as:</p>"
        f"<ul>{''.join(rows)}</ul>"
    ).encode()


def _picture_or_404(
    start_response: StartResponse, path: str
) -> Iterable[bytes]:
    for user in DEV_USERS.values():
        if user.colour and path == f"/api/users/{user.sub}/profile-picture.png":
            start_response("200 OK", [("Content-Type", "image/png")])
            return [picture_png(user)]
    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"not found"]


def wsgi_app(
    provider: FakeProvider,
) -> Callable[[WSGIEnvironment, StartResponse], Iterable[bytes]]:
    """Wraps a provider in a WSGI app for the dev server."""

    def app(
        environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        path = environ["PATH_INFO"]
        query = dict(urllib.parse.parse_qsl(environ.get("QUERY_STRING", "")))
        auth = environ.get("HTTP_AUTHORIZATION")
        if path == "/.well-known/openid-configuration":
            return _respond_json(start_response, 200, provider.discovery())
        if path == "/.well-known/jwks.json":
            return _respond_json(start_response, 200, provider.jwks())
        if path == "/authorize":
            handle = query.pop("as", None)
            if handle not in DEV_USERS:
                start_response("200 OK", [("Content-Type", "text/html")])
                return [_picker(query)]
            return _redirect(
                start_response, provider.authorize(query, DEV_USERS[handle])
            )
        if path == "/api/oidc/token":
            length = int(environ.get("CONTENT_LENGTH") or 0)
            body = environ["wsgi.input"].read(length).decode()
            form = dict(urllib.parse.parse_qsl(body))
            return _respond_json(start_response, *provider.token(form, auth))
        if path == "/api/oidc/userinfo":
            return _respond_json(start_response, *provider.userinfo(auth))
        if path == "/api/oidc/end-session":
            target = query.get("post_logout_redirect_uri", "/")
            return _redirect(start_response, target)
        return _picture_or_404(start_response, path)

    return app


def main() -> None:
    """Serves the fake provider on 127.0.0.1:8001 for `just dev`."""
    provider = FakeProvider(
        "http://127.0.0.1:8001", "reellife-dev", "reellife-dev-secret"
    )
    with simple_server.make_server(
        "127.0.0.1", 8001, wsgi_app(provider)
    ) as server:
        print("Fake OIDC provider on http://127.0.0.1:8001")  # noqa: T201
        server.serve_forever()


if __name__ == "__main__":
    main()
