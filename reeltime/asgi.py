"""ASGI entry point: Django, plus the MCP server at /mcp.

gunicorn serves this with its ASGI worker. Requests for /mcp and its
OAuth protected-resource metadata go to the MCP server; everything else
to Django, whose middleware (HTTPS redirect, CSP, sessions, roles)
applies as before, behind a cap on request bodies (REQUEST_BODY_MAX,
more for an admin restoring a backup). The MCP side checks Host and
Origin itself, caps bodies at MCP_BODY_MAX, refuses plain HTTP where
production expects HTTPS, limits each client address to
RATELIMIT_MCP_PER_ADDRESS_PER_MINUTE requests and each signed-in user's
client to RATELIMIT_MCP_PER_MINUTE, and needs a PocketID token. Defaults
to production settings.
"""

import hashlib
import importlib
import os
import time
from typing import Any
import urllib.parse

from asgiref.sync import sync_to_async
import django
from django.conf import settings
from django.contrib import auth as django_auth
from django.core.asgi import get_asgi_application
from django.core.cache import cache
from django.db import transaction
from django.http import HttpRequest
from django.http.cookie import parse_cookie
from django.urls import reverse
from starlette.responses import PlainTextResponse

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "reeltime.settings.prod")
django.setup(set_prefix=False)
django_app = get_asgi_application()

# pylint: disable=wrong-import-position
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

from accounts import roles  # noqa: E402
from accounts.models import Role  # noqa: E402
from cues.exporters import backup  # noqa: E402
from mcp_server import auth as mcp_auth  # noqa: E402
from mcp_server import server as mcp_server  # noqa: E402
from mcp_server import transfers  # noqa: E402
from reeltime import net  # noqa: E402

MCP_PATH = "/mcp"
METADATA_PREFIX = "/.well-known/oauth-protected-resource"
_MIB = 1024 * 1024
# Request body limits. Django's ASGI handler reads a whole body (spooling
# it to TMPDIR) before any middleware runs, and gunicorn has no limit of
# its own, so the router enforces them first. MCP bodies are capped by
# the SDK at MCP_BODY_MAX: an import_cues call with the largest file
# transfers.py accepts, plus room for the rest of the JSON-RPC message.
MCP_BODY_MAX = transfers.MAX_BASE64 + _MIB
# A backup being restored (cues/views/backup.py), plus multipart framing.
# Only a signed-in admin's POST to the restore page gets this much.
RESTORE_BODY_MAX = backup.MAX_UPLOAD + _MIB

Scope = dict[str, Any]


def _hosts() -> list[str]:
    """Host headers the MCP side accepts: ALLOWED_HOSTS, with any port."""
    hosts = []
    for host in settings.ALLOWED_HOSTS:
        hosts += [host, f"{host}:*"]
    return hosts


def _origin() -> str:
    parts = urllib.parse.urlsplit(settings.BASE_URL)
    return f"{parts.scheme}://{parts.netloc}"


def build_mcp_app() -> Any:
    """The MCP server's ASGI app, configured from settings."""
    return mcp_server.build().streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        max_request_body_size=MCP_BODY_MAX,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_hosts(),
            allowed_origins=[_origin()],
        ),
    )


def _headers(scope: Scope, name: bytes, sep: str = ",") -> str:
    """A request header's values, joined as Django joins them."""
    return sep.join(
        value.decode("latin-1")
        for key, value in scope.get("headers", [])
        if key.lower() == name
    )


def _address(scope: Scope) -> str:
    """The client's address, honouring TRUST_PROXY as the web app does."""
    remote = str((scope.get("client") or ("",))[0])
    return net.address(remote, _headers(scope, b"x-forwarded-for"))


def _bearer(scope: Scope) -> str:
    """The bearer token, or '' (the scheme's case doesn't matter)."""
    scheme, _, token = _headers(scope, b"authorization").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _count(key: str) -> int:
    """Adds one to a counter in the shared cache and returns it.

    Django's database cache increments with a read then a write. In a
    transaction they are atomic: SQLite runs ReelTime's transactions as
    BEGIN IMMEDIATE (settings.base.sqlite_database), which takes the
    database's write lock first, so workers count one at a time.
    """
    with transaction.atomic():
        count = int(cache.get(key, 0)) + 1
        cache.set(key, count, timeout=90)
    return count


def _limited(kind: str, who: str, per_minute: int) -> int:
    """Counts a request; seconds to wait if `who` is over the limit, else 0.

    Args:
        kind: What `who` is ('address' or 'user'); keeps counts apart.
        who: The client, hashed before it becomes a cache key.
        per_minute: Requests allowed in each clock minute.
    """
    now = time.time()
    digest = hashlib.sha256(who.encode()).hexdigest()[:32]
    if _count(f"mcp-rate:{kind}:{digest}:{int(now // 60)}") > per_minute:
        return 60 - int(now) % 60
    return 0


async def _too_many(scope: Scope, receive: Any, send: Any, wait: int) -> None:
    response = PlainTextResponse(
        "Too many requests; wait a moment.",
        status_code=429,
        headers={"Retry-After": str(wait)},
    )
    await response(scope, receive, send)


def _signed_in_admin(scope: Scope) -> bool:
    """Whether the request's session cookie is a signed-in admin's.

    Only decides how big a body may be; the view checks everything again.
    """
    cookies = parse_cookie(_headers(scope, b"cookie", "; "))
    key = cookies.get(settings.SESSION_COOKIE_NAME)
    if not key:
        return False
    engine = importlib.import_module(settings.SESSION_ENGINE)
    request = HttpRequest()
    request.session = engine.SessionStore(key)
    user = django_auth.get_user(request)
    return roles.at_least(getattr(user, "role", Role.NONE), Role.ADMIN)


async def _web_body_max(scope: Scope) -> int:
    """The largest body Django may receive for this request."""
    if (
        scope.get("method") == "POST"
        and scope.get("path") == reverse("cues:backup")
        and await sync_to_async(_signed_in_admin)(scope)
    ):
        return RESTORE_BODY_MAX
    return int(settings.REQUEST_BODY_MAX)


async def _too_large(scope: Scope, receive: Any, send: Any) -> None:
    response = PlainTextResponse("Request body too large.", status_code=413)
    await response(scope, receive, send)


async def _capped(
    app: Any, scope: Scope, receive: Any, send: Any, limit: int
) -> None:
    """Runs `app` with request bodies over `limit` bytes refused (413).

    A Content-Length over the limit is refused before reading anything.
    Otherwise bytes are counted as they arrive; past the limit the
    client gets 413 and the app sees the client disconnect (Django then
    drops the request), and anything else the app sends is discarded.
    """
    try:
        declared = int(_headers(scope, b"content-length") or 0)
    except ValueError:
        declared = 0  # Not a number: count what actually arrives.
    if declared > limit:
        await _too_large(scope, receive, send)
        return
    received = 0
    refused = started = False

    async def counted_receive() -> Any:
        nonlocal received, refused
        if refused:
            return {"type": "http.disconnect"}
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                refused = True
                if not started:
                    await _too_large(scope, receive, send)
                return {"type": "http.disconnect"}
        return message

    async def guarded_send(message: Any) -> None:
        nonlocal started
        if not refused:
            started = True
            await send(message)

    await app(scope, counted_receive, guarded_send)


def _is_mcp(path: str) -> bool:
    return path in {MCP_PATH, f"{MCP_PATH}/"} or path.startswith(
        METADATA_PREFIX
    )


def _secure(scope: Scope) -> bool:
    """Whether the request arrived over HTTPS, where that matters."""
    if not settings.SECURE_SSL_REDIRECT:
        return True
    if scope.get("scheme") == "https":
        return True
    header = getattr(settings, "SECURE_PROXY_SSL_HEADER", None)
    if header:
        name = header[0].removeprefix("HTTP_").replace("_", "-").lower()
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == name:
                return bool(value.decode("latin-1") == header[1])
    return False


class Router:  # pylint: disable=too-few-public-methods
    """Sends MCP requests to the MCP app and the rest to Django."""

    def __init__(self, mcp_app: Any, web_app: Any) -> None:
        """Holds the two apps."""
        self.mcp_app = mcp_app
        self.web_app = web_app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        """Routes one ASGI connection."""
        if scope["type"] == "lifespan":
            # Only the MCP app has startup work (its session manager).
            await self.mcp_app(scope, receive, send)
            return
        if scope["type"] == "http" and _is_mcp(scope.get("path", "")):
            if not _secure(scope):
                response = PlainTextResponse("Use HTTPS.", status_code=403)
                await response(scope, receive, send)
                return
            with mcp_auth.one_request():
                await self._mcp(scope, receive, send)
            return
        if scope["type"] == "http":
            limit = await _web_body_max(scope)
            await _capped(self.web_app, scope, receive, send, limit)
            return
        await self.web_app(scope, receive, send)

    async def _mcp(self, scope: Scope, receive: Any, send: Any) -> None:
        """Rate-limits an MCP request, then hands it to the MCP app.

        Every request counts against its address first, so made-up
        tokens can't buy fresh allowances. A token that verifies then
        counts against its user and client, however it is spelled and
        however often the client gets a new one.
        """
        wait = await sync_to_async(_limited)(
            "address",
            _address(scope),
            settings.RATELIMIT_MCP_PER_ADDRESS_PER_MINUTE,
        )
        token = _bearer(scope)
        access = await mcp_auth.verify(token) if token and not wait else None
        if access is not None:
            wait = await sync_to_async(_limited)(
                "user",
                f"{access.subject}\n{access.client_id}",
                settings.RATELIMIT_MCP_PER_MINUTE,
            )
        if wait:
            await _too_many(scope, receive, send, wait)
            return
        await self.mcp_app(scope, receive, send)


application = Router(build_mcp_app(), django_app)
