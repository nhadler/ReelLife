"""Sign-in through an OpenID Connect provider such as PocketID.

Uses mozilla-django-oidc with the following checks:

- Endpoints come from the issuer's discovery document, fetched when a
  sign-in starts and cached, never at import or on ordinary requests.
- ID tokens must be RS256 or ES256, signed by a key from the provider's
  JWKS, with matching `iss`, `aud`, `nonce` and an unexpired `exp`.
- PKCE is required.
- Users are keyed on `sub`, never email, and get their role from the
  `groups` claim at every sign-in. No ReelTime group means no access.
"""

import dataclasses
import hashlib
import logging
from typing import Any
import urllib.parse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse
import jwt
from mozilla_django_oidc import auth as oidc_auth
import requests

from accounts import audit
from accounts import models
from accounts import pictures
from accounts import roles

_LOGGER = logging.getLogger(__name__)

ALLOWED_ALGORITHMS = ("RS256", "ES256")
# Tolerated clock difference between ReelTime and the provider, seconds.
CLOCK_LEEWAY = 60
_DISCOVERY_CACHE_KEY = "accounts.oidc.discovery"
_DISCOVERY_TTL = 60 * 60
_USERNAME_PREFIX = "oidc-"
# Where the session keeps the tokens (mozilla-django-oidc stores the first
# two). Sessions are server-side, so tokens never reach the browser.
STORED_ACCESS = "oidc_access_token"
STORED_ID = "oidc_id_token"
STORED_REFRESH = "oidc_refresh_token"
# Token endpoint and userinfo responses indicating an invalid token.
_TOKEN_REFUSED = frozenset({400, 401, 403})


class DiscoveryError(Exception):
    """The provider's discovery document couldn't be fetched or trusted."""


class LoginRefusedError(Exception):
    """A valid identity that may not use ReelTime.

    Attributes:
        reason: Machine-readable reason for the audit log.
        sub: The OIDC subject that was refused.
    """

    def __init__(self, reason: str, sub: str) -> None:
        """Records why and for whom."""
        super().__init__(reason)
        self.reason = reason
        self.sub = sub


@dataclasses.dataclass(frozen=True)
class ProviderConfig:
    """The parts of the discovery document ReelTime uses."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None = None


def provider_config() -> ProviderConfig:
    """Returns the provider's endpoints, fetching discovery if needed.

    Raises:
        DiscoveryError: If discovery fails or the document's issuer doesn't
            match OIDC_ISSUER.
    """
    cached = cache.get(_DISCOVERY_CACHE_KEY)
    if isinstance(cached, dict):
        return ProviderConfig(**cached)
    config = _fetch_discovery(settings.OIDC_ISSUER)
    cache.set(_DISCOVERY_CACHE_KEY, dataclasses.asdict(config), _DISCOVERY_TTL)
    return config


def _fetch_discovery(issuer: str) -> ProviderConfig:
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        response = requests.get(url, timeout=settings.OIDC_TIMEOUT)
        response.raise_for_status()
        doc = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DiscoveryError(f"discovery failed: {url}") from exc
    if not isinstance(doc, dict):
        raise DiscoveryError("discovery document is not an object")
    # OIDC Discovery 1.0 section 4.3: the issuer must match exactly; we
    # ignore a trailing slash in our own configuration.
    if doc.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
        raise DiscoveryError("discovery issuer does not match OIDC_ISSUER")
    try:
        return ProviderConfig(
            issuer=str(doc["issuer"]),
            authorization_endpoint=str(doc["authorization_endpoint"]),
            token_endpoint=str(doc["token_endpoint"]),
            userinfo_endpoint=str(doc["userinfo_endpoint"]),
            jwks_uri=str(doc["jwks_uri"]),
            end_session_endpoint=doc.get("end_session_endpoint"),
        )
    except KeyError as exc:
        raise DiscoveryError(f"discovery lacks {exc.args[0]}") from exc


def callback_url() -> str:
    """Returns the redirect URI registered with the provider."""
    return settings.BASE_URL.rstrip("/") + reverse("accounts:oidc_callback")


def _username_for(sub: str) -> str:
    username = _USERNAME_PREFIX + sub
    if len(username) <= 150:
        return username
    return _USERNAME_PREFIX + hashlib.sha256(sub.encode()).hexdigest()


def _str_claim(claims: dict[str, Any], name: str, limit: int) -> str:
    value = claims.get(name)
    return value[:limit] if isinstance(value, str) else ""


class Backend(oidc_auth.OIDCAuthenticationBackend):  # type: ignore[misc]
    """Authenticates the OIDC authorization code callback."""

    # mozilla-django-oidc reads endpoints from settings in __init__. Django
    # builds a backend on every request to load the session's user, so
    # endpoints are resolved lazily from discovery instead.
    def __init__(self, *args: Any, **kwargs: Any) -> None:  # pylint: disable=super-init-not-called
        """Sets client credentials; endpoints are looked up on use."""
        del args, kwargs
        self.UserModel = get_user_model()  # pylint: disable=invalid-name
        self.OIDC_RP_CLIENT_ID = settings.OIDC_RP_CLIENT_ID  # pylint: disable=invalid-name
        self.OIDC_RP_CLIENT_SECRET = settings.OIDC_RP_CLIENT_SECRET  # pylint: disable=invalid-name
        self.request: HttpRequest | None = None
        self._config: ProviderConfig | None = None

    @property
    def config(self) -> ProviderConfig:
        """The provider configuration, fetched once per backend."""
        if self._config is None:
            self._config = provider_config()
        return self._config

    @property
    def OIDC_OP_TOKEN_ENDPOINT(self) -> str:  # noqa: N802 pylint: disable=invalid-name
        """Token endpoint, as the base class expects it."""
        return self.config.token_endpoint

    @property
    def OIDC_OP_USER_ENDPOINT(self) -> str:  # noqa: N802 pylint: disable=invalid-name
        """Userinfo endpoint, as the base class expects it."""
        return self.config.userinfo_endpoint

    @property
    def OIDC_OP_JWKS_ENDPOINT(self) -> str:  # noqa: N802 pylint: disable=invalid-name
        """JWKS endpoint, as the base class expects it."""
        return self.config.jwks_uri

    def authenticate(  # pylint: disable=arguments-differ
        self, request: HttpRequest | None, **kwargs: Any
    ) -> models.User | None:
        """Completes a sign-in from the provider's callback.

        Args:
            request: The callback request, carrying `code` and `state`.
            **kwargs: `nonce` and `code_verifier` from the session, passed
                by the callback view.

        Returns:
            The signed-in user, or None if this isn't an OIDC callback or
            the sign-in failed or was refused (both are audit-logged; a
            refusal also sets `request.reeltime_login_refused`).
        """
        if request is None:
            return None
        code = request.GET.get("code")
        if not code or not request.GET.get("state"):
            return None
        self.request = request
        try:
            user = self._complete_login(
                request,
                code,
                nonce=kwargs.get("nonce"),
                code_verifier=kwargs.get("code_verifier"),
            )
        except LoginRefusedError as exc:
            audit.record(
                models.AuditAction.LOGIN_REFUSED,
                request=request,
                actor=exc.sub,
                reason=exc.reason,
            )
            setattr(request, "reeltime_login_refused", exc.reason)  # noqa: B010
            return None
        except (
            SuspiciousOperation,
            DiscoveryError,
            requests.RequestException,
            ValueError,
        ) as exc:
            _LOGGER.warning("OIDC sign-in failed: %s", exc)
            audit.record(
                models.AuditAction.LOGIN_FAILED,
                request=request,
                reason=type(exc).__name__,
            )
            return None
        audit.record(
            models.AuditAction.LOGIN, request=request, user=user, method="oidc"
        )
        return user

    def _complete_login(
        self,
        request: HttpRequest,
        code: str,
        *,
        nonce: str | None,
        code_verifier: str | None,
    ) -> models.User:
        if not code_verifier:
            raise SuspiciousOperation("PKCE code verifier missing")
        token_info = self.get_token(
            {
                "client_id": self.OIDC_RP_CLIENT_ID,
                "client_secret": self.OIDC_RP_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url(),
                "code_verifier": code_verifier,
            }
        )
        id_token = token_info.get("id_token")
        access_token = token_info.get("access_token")
        if not isinstance(id_token, str) or not isinstance(access_token, str):
            raise SuspiciousOperation("token response lacks tokens")
        payload = self.verify_token(id_token, nonce=nonce)
        claims = self._merged_claims(access_token, id_token, payload)
        self.store_tokens(access_token, id_token)
        refresh_token = token_info.get("refresh_token")
        if isinstance(refresh_token, str):
            request.session[STORED_REFRESH] = refresh_token
        user = self._sync_user(claims)
        # Here rather than in _sync_user, which MCP calls every few minutes.
        pictures.refresh(user, claims.get("picture"))
        user.save(update_fields=["picture", "picture_hash"])
        return user

    def session_userinfo(self, request: HttpRequest) -> dict[str, Any] | None:
        """Userinfo for the session's tokens, refreshing them if needed.

        Args:
            request: The request whose session holds the tokens.

        Returns:
            The userinfo claims, or None if the provider refuses the
            tokens and they can't be refreshed.

        Raises:
            DiscoveryError: The provider's endpoints are unknown.
            requests.RequestException: The provider couldn't be asked.
            ValueError: It answered with something other than JSON.
        """
        token = request.session.get(STORED_ACCESS)
        if isinstance(token, str):
            info = self._userinfo(token)
            if info is not None:
                return info
        token = self._refresh(request)
        return None if token is None else self._userinfo(token)

    def _userinfo(self, access_token: str) -> dict[str, Any] | None:
        response = requests.get(
            self.OIDC_OP_USER_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=settings.OIDC_TIMEOUT,
        )
        if response.status_code in _TOKEN_REFUSED:
            return None
        response.raise_for_status()
        info = response.json()
        if not isinstance(info, dict):
            raise ValueError("userinfo is not an object")
        return info

    def _refresh(self, request: HttpRequest) -> str | None:
        """A new access token from the stored refresh token, if any."""
        refresh_token = request.session.get(STORED_REFRESH)
        if not isinstance(refresh_token, str):
            return None
        response = requests.post(
            self.OIDC_OP_TOKEN_ENDPOINT,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.OIDC_RP_CLIENT_ID,
                "client_secret": self.OIDC_RP_CLIENT_SECRET,
            },
            timeout=settings.OIDC_TIMEOUT,
        )
        if response.status_code in _TOKEN_REFUSED:
            request.session.pop(STORED_REFRESH, None)
            return None
        response.raise_for_status()
        body = response.json()
        access_token = (
            body.get("access_token") if isinstance(body, dict) else None
        )
        if not isinstance(access_token, str):
            raise ValueError("refresh response lacks an access token")
        request.session[STORED_ACCESS] = access_token
        # Providers that rotate refresh tokens send a new one each time.
        if isinstance(body.get("refresh_token"), str):
            request.session[STORED_REFRESH] = body["refresh_token"]
        return access_token

    def get_user(self, user_id: int) -> models.User | None:
        """Loads the session's user, unless they've been deactivated.

        Args:
            user_id: The primary key stored in the session.

        Returns:
            The user, or None (so the request is anonymous) if they don't
            exist or are inactive.
        """
        user = models.User.objects.filter(pk=user_id).first()
        return user if user is not None and user.is_active else None

    def verify_token(self, token: str, **kwargs: Any) -> dict[str, Any]:
        """Verifies an ID token's signature and claims.

        Args:
            token: The encoded ID token.
            **kwargs: `nonce`, the value sent in the authorization request.

        Returns:
            The token's claims.

        Raises:
            SuspiciousOperation: If anything about the token is wrong.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise SuspiciousOperation("malformed ID token") from exc
        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise SuspiciousOperation(f"ID token alg {alg!r} not allowed")
        try:
            key = self.retrieve_matching_jwk(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=self.OIDC_RP_CLIENT_ID,
                issuer=self.config.issuer,
                leeway=CLOCK_LEEWAY,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, KeyError) as exc:
            raise SuspiciousOperation(f"ID token rejected: {exc}") from exc
        nonce = kwargs.get("nonce")
        if not nonce or claims.get("nonce") != nonce:
            raise SuspiciousOperation("ID token nonce mismatch")
        audience = claims["aud"]
        if isinstance(audience, list) and len(audience) > 1:
            if claims.get("azp") != self.OIDC_RP_CLIENT_ID:
                raise SuspiciousOperation("ID token azp mismatch")
        return claims

    def _merged_claims(
        self, access_token: str, id_token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        info = self.get_userinfo(access_token, id_token, payload)
        if not isinstance(info, dict):
            raise SuspiciousOperation("userinfo is not an object")
        # OIDC Core 5.3.2: userinfo must describe the same subject.
        if info.get("sub") != payload["sub"]:
            raise SuspiciousOperation("userinfo sub mismatch")
        # Providers put groups in the ID token, userinfo or both; userinfo
        # wins when present because it is fetched fresh.
        return {**payload, **info}

    def _sync_user(self, claims: dict[str, Any]) -> models.User:
        """Creates or updates the user for `claims`, role from groups.

        Called at every sign-in, by session checks (accounts/sessions.py)
        and for MCP tokens (mcp_server/auth.py).

        Raises:
            SuspiciousOperation: No usable `sub`.
            LoginRefusedError: No ReelTime group (a known user's role is
                then cleared and stays cleared), or an inactive user.
        """
        sub = claims["sub"]
        if not isinstance(sub, str) or not sub:
            raise SuspiciousOperation("sub claim missing")
        role = roles.role_from_groups(
            claims.get("groups"),
            admin_group=settings.OIDC_GROUP_ADMIN,
            editor_group=settings.OIDC_GROUP_EDITOR,
            viewer_group=settings.OIDC_GROUP_VIEWER,
        )
        if role == models.Role.NONE:
            # Saved before refusing, outside any transaction the refusal
            # rolls back, so their open sessions and MCP clients lose
            # access too (RoleMiddleware refuses role none).
            self._clear_role(sub)
            raise LoginRefusedError("no_group", sub)
        with transaction.atomic():
            user = models.User.objects.filter(oidc_sub=sub).first()
            if user is not None and not user.is_active:
                # Deactivated in ReelTime's admin: stays out whatever
                # PocketID says, until an admin turns the account back on.
                raise LoginRefusedError("inactive", sub)
            previous_role = None if user is None else user.role
            if user is None:
                user = models.User(oidc_sub=sub, username=_username_for(sub))
                user.set_unusable_password()
            user.email = _str_claim(claims, "email", 254)
            user.first_name = _str_claim(
                claims, "given_name", 150
            ) or _str_claim(claims, "name", 150)
            user.last_name = _str_claim(claims, "family_name", 150)
            user.role = role
            user.is_staff = user.is_superuser = role == models.Role.ADMIN
            user.save()
            if previous_role is not None:
                self._audit_role(user, previous_role)
        return user

    def _clear_role(self, sub: str) -> None:
        user = models.User.objects.filter(oidc_sub=sub).first()
        if user is None or user.role == models.Role.NONE:
            return
        previous_role = user.role
        user.role = models.Role.NONE
        user.is_staff = user.is_superuser = False
        user.save(update_fields=["role", "is_staff", "is_superuser"])
        self._audit_role(user, previous_role)

    def _audit_role(self, user: models.User, previous_role: str) -> None:
        if previous_role != user.role:
            audit.record(
                models.AuditAction.ROLE_CHANGED,
                request=self.request,
                user=user,
                old=previous_role,
                new=models.Role(user.role).value,
            )


def provider_logout_url(request: HttpRequest) -> str | None:
    """Returns the provider's end-session URL for an OIDC user, if any.

    Args:
        request: The logout request, before the session is cleared.

    Returns:
        A URL that ends the provider session and returns to the sign-in
        page, or None if the provider has no end_session_endpoint (or the
        user signed in locally).
    """
    if not getattr(request.user, "oidc_sub", None):
        return None
    try:
        endpoint = provider_config().end_session_endpoint
    except DiscoveryError:
        return None
    if not endpoint:
        return None
    params = {
        "client_id": settings.OIDC_RP_CLIENT_ID,
        "post_logout_redirect_uri": settings.BASE_URL.rstrip("/")
        + reverse("accounts:login"),
    }
    id_token = request.session.get(STORED_ID)
    if id_token:
        params["id_token_hint"] = id_token
    return f"{endpoint}?{urllib.parse.urlencode(params)}"


def check_session(request: HttpRequest) -> bool:
    """Asks the provider whether a signed-in user may stay signed in.

    Uses the access token from sign-in (refreshed with the refresh token
    when the provider issued one and the access token has expired) to
    fetch userinfo, then applies the same rules as a sign-in: a user in
    no ReelTime group loses their role, a role change is saved and
    audited. Called every SESSION_CHECK_INTERVAL by
    accounts.sessions.SessionTimeoutMiddleware.

    Args:
        request: The current request, with a signed-in user.

    Returns:
        False to sign the session out: the provider refused the tokens
        (expired with no way to refresh, or revoked), describes someone
        else, or the user may no longer use ReelTime. True otherwise,
        including when the provider can't be reached: an outage only
        blocks new sign-ins, and the check is tried again later.
        Break-glass sessions aren't OIDC sessions and are always kept.
    """
    sub = getattr(request.user, "oidc_sub", None)
    if not sub:
        return True
    backend = Backend()
    backend.request = request
    try:
        info = backend.session_userinfo(request)
    except (DiscoveryError, requests.RequestException, ValueError) as exc:
        _LOGGER.warning("Session check postponed: %s", type(exc).__name__)
        return True
    if info is None or info.get("sub") != sub:
        _LOGGER.info("Session ended: the provider refused its tokens")
        return False
    try:
        backend._sync_user(  # pylint: disable=protected-access
            {**_stored_id_claims(request), **info}
        )
    except LoginRefusedError as exc:
        audit.record(
            models.AuditAction.LOGIN_REFUSED,
            request=request,
            actor=exc.sub,
            reason=f"session_{exc.reason}",
        )
        return False
    return True


def _stored_id_claims(request: HttpRequest) -> dict[str, Any]:
    """Claims of the ID token verified at sign-in, kept in the session.

    Like a sign-in, groups come from userinfo when it has them, else from
    the ID token. The token was verified when stored and the session is
    server-side, so it isn't verified again (it has expired by now).
    """
    token = request.session.get(STORED_ID)
    if not isinstance(token, str):
        return {}
    try:
        claims: dict[str, Any] = jwt.decode(
            token, options={"verify_signature": False}
        )
    except jwt.PyJWTError:
        return {}
    return claims
