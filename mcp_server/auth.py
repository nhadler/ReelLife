"""Who an MCP client acts for (decided Oct 31).

MCP clients sign in through PocketID and send its access token. Each
token is checked here, in ReelLife: signed by a key from PocketID's
JWKS, issued by OIDC_ISSUER, unexpired, and issued for ReelLife's MCP
resource (its `aud` is `resource_url()`, which PocketID sets when the
resource is registered under its APIs). The user's groups come from
PocketID's userinfo and become their role exactly as at a web sign-in;
userinfo is asked again after ROLE_TTL seconds, so a change in PocketID
reaches MCP within that time; a user deactivated in ReelLife, or found
without a group at a web sign-in, is refused at once. No ReelLife group,
no access.
"""

from collections.abc import Iterator
import contextlib
import contextvars
import dataclasses
import hashlib
import logging
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation
from django.db import close_old_connections
import jwt
from mcp.server.auth.provider import AccessToken
import requests

from accounts import audit
from accounts import models as account_models
from accounts import oidc

_LOGGER = logging.getLogger(__name__)

ROLE_TTL = 5 * 60
_CACHE_PREFIX = "mcp_server.auth.user."
_JWKS_TTL = 60 * 60
_jwks: dict[str, jwt.PyJWKClient] = {}
# This request's token and what `check` made of it; see `verify`.
_verified: contextvars.ContextVar[tuple[str, AccessToken | None] | None] = (
    contextvars.ContextVar("mcp_verified", default=None)
)


def resource_url() -> str:
    """ReelLife's MCP endpoint, which tokens must be issued for."""
    return f"{settings.BASE_URL.rstrip('/')}/mcp"


@dataclasses.dataclass(frozen=True)
class Caller:
    """The ReelLife user an MCP request acts for."""

    user_id: int
    role: str


def _signing_key(token: str, jwks_uri: str) -> Any:
    # The key set is cached for _JWKS_TTL, then fetched again; keys are
    # looked up in it each time (cache_keys=False), so a key PocketID
    # retires stops verifying tokens once the set is refreshed.
    client = _jwks.get(jwks_uri)
    if client is None:
        client = jwt.PyJWKClient(jwks_uri, cache_keys=False, lifespan=_JWKS_TTL)
        _jwks[jwks_uri] = client
    return client.get_signing_key_from_jwt(token).key


def _claims(token: str) -> dict[str, Any]:
    """The token's claims, once its signature and claims check out.

    Raises:
        jwt.PyJWTError: The token isn't one PocketID issued for ReelLife.
    """
    alg = jwt.get_unverified_header(token).get("alg")
    if alg not in oidc.ALLOWED_ALGORITHMS:
        raise jwt.InvalidAlgorithmError(f"alg {alg!r} not allowed")
    config = oidc.provider_config()
    claims: dict[str, Any] = jwt.decode(
        token,
        _signing_key(token, config.jwks_uri),
        algorithms=[alg],
        audience=resource_url(),
        issuer=config.issuer,
        leeway=oidc.CLOCK_LEEWAY,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise jwt.InvalidTokenError("sub missing")
    return claims


def _caller(token: str, sub: str) -> Caller:
    """The user and role for `sub`, from cache or PocketID's userinfo.

    Raises:
        oidc.LoginRefusedError: The user is in no ReelLife group, or
            is inactive.
        SuspiciousOperation: Userinfo describes someone else.
        requests.RequestException: PocketID couldn't be asked.
    """
    key = _CACHE_PREFIX + hashlib.sha256(sub.encode()).hexdigest()
    cached = cache.get(key)
    if isinstance(cached, dict):
        # The cache only spares PocketID a call; the user is read afresh,
        # so deactivating them (or a sign-in that found no group) counts
        # at once. Otherwise ask PocketID again, which refuses them.
        user = account_models.User.objects.filter(
            pk=Caller(**cached).user_id, is_active=True
        ).first()
        if user is not None and user.role != account_models.Role.NONE:
            return Caller(user_id=user.pk, role=user.role)
        cache.delete(key)
    config = oidc.provider_config()
    response = requests.get(
        config.userinfo_endpoint,
        headers={"Authorization": f"Bearer {token}"},
        timeout=settings.OIDC_TIMEOUT,
    )
    response.raise_for_status()
    info = response.json()
    if not isinstance(info, dict) or info.get("sub") != sub:
        raise SuspiciousOperation("userinfo sub mismatch")
    # The same rules as a web sign-in: keyed on sub, role from groups.
    user = oidc.Backend()._sync_user(info)  # pylint: disable=protected-access
    caller = Caller(user_id=user.pk, role=user.role)
    cache.set(key, dataclasses.asdict(caller), ROLE_TTL)
    return caller


def check(token: str) -> AccessToken | None:
    """An AccessToken for a good token, else None."""
    try:
        claims = _claims(token)
        caller = _caller(token, claims["sub"])
    except oidc.LoginRefusedError as exc:
        audit.record(
            account_models.AuditAction.LOGIN_REFUSED,
            actor=exc.sub,
            reason=f"mcp_{exc.reason}",
        )
        return None
    except (
        jwt.PyJWTError,
        oidc.DiscoveryError,
        SuspiciousOperation,
        requests.RequestException,
        ValueError,
    ) as exc:
        # Unsigned or foreign tokens are anyone's noise: logged, not
        # audited. Refusing a real user (above) is audited.
        _LOGGER.warning("MCP token refused: %s", type(exc).__name__)
        return None
    client = claims.get("client_id") or claims.get("azp") or ""
    scope = claims.get("scope")
    return AccessToken(
        token=token,
        client_id=str(client),
        scopes=scope.split() if isinstance(scope, str) else [],
        expires_at=int(claims["exp"]),
        resource=resource_url(),
        subject=claims["sub"],
        claims={
            "iss": claims["iss"],
            "user_id": caller.user_id,
            "role": caller.role,
        },
    )


@contextlib.contextmanager
def one_request() -> Iterator[None]:
    """Scopes `verify`'s memory to one request (the router's job).

    A worker may serve several requests in one task, one after another
    on a kept-alive connection; each must check its token afresh.
    """
    reset = _verified.set(None)
    try:
        yield
    finally:
        _verified.reset(reset)


def _check_in_thread(token: str) -> AccessToken | None:
    """check(), handling the thread's database connection like a request."""
    close_old_connections()
    try:
        return check(token)
    finally:
        close_old_connections()


async def verify(token: str) -> AccessToken | None:
    """`check` for this request, run off the event loop (it uses the ORM).

    The answer is remembered for the rest of the request, so the router
    (reellife/asgi.py, which rate-limits by the verified user) and the
    SDK's TokenVerifier check each token once.
    """
    known = _verified.get()
    if known is not None and known[0] == token:
        return known[1]
    # A pool thread, as for tools (common.off_loop): the userinfo call can
    # wait on PocketID for seconds, and shouldn't hold up other requests.
    access = await sync_to_async(_check_in_thread, thread_sensitive=False)(
        token
    )
    _verified.set((token, access))
    return access


class TokenVerifier:  # pylint: disable=too-few-public-methods
    """The SDK's hook: checks each request's bearer token."""

    async def verify_token(self, token: str) -> AccessToken | None:
        """See `verify`."""
        return await verify(token)
