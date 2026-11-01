"""Request bodies are capped before Django reads them (reellife/asgi.py).

Django's ASGI handler spools a whole body to disk before middleware
runs, so the router refuses oversized ones first.
"""

import asyncio
import json
from typing import Any

from django.conf import settings
from django.test import Client
import httpx2
from mcp.server.auth.provider import AccessToken
import pytest

from accounts.models import Role
from accounts.models import User
from mcp_server import auth
from mcp_server import transfers
from reellife import asgi

pytestmark = pytest.mark.django_db(transaction=True)

LIMIT = 100


class _Web:
    """A stand-in for Django: reads the body, then answers 200."""

    def __init__(self, answer_first: bool = False) -> None:
        self.answer_first = answer_first
        self.bodies: list[bytes] = []
        self.ended_by: list[str] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        del scope
        if self.answer_first:
            await send({"type": "http.response.start", "status": 200})
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                self.ended_by.append(message["type"])
                break
            body += message.get("body", b"")
            if not message.get("more_body"):
                self.ended_by.append("end")
                break
        self.bodies.append(body)
        # Django then listens for the client going away.
        self.ended_by.append((await receive())["type"])
        if not self.answer_first:
            await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"ok"})


def _post(
    web: _Web,
    chunks: list[bytes],
    *,
    path: str = "/projects/new/",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[Any]:
    """Posts `chunks` through the router; returns what it sent back."""
    sent: list[Any] = []
    queue = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": n < len(chunks) - 1,
        }
        for n, chunk in enumerate(chunks)
    ]

    async def receive() -> Any:
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "scheme": "https",
        "headers": headers or [],
        "client": ("10.0.0.1", 5000),
    }
    asyncio.run(asgi.Router(None, web)(scope, receive, send))
    return sent


def _status(sent: list[Any]) -> int:
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1
    return int(starts[0]["status"])


@pytest.fixture(autouse=True)
def _small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REQUEST_BODY_MAX", LIMIT)
    monkeypatch.setattr(asgi, "RESTORE_BODY_MAX", 3 * LIMIT)


def test_a_body_within_the_limit_reaches_django() -> None:
    web = _Web()
    sent = _post(web, [b"a" * 60, b"b" * 40])
    assert _status(sent) == 200
    assert web.bodies == [b"a" * 60 + b"b" * 40]
    assert web.ended_by == ["end", "http.disconnect"]


def test_a_declared_length_over_the_limit_is_refused_unread() -> None:
    web = _Web()
    sent = _post(web, [b"x"], headers=[(b"content-length", b"101")])
    assert _status(sent) == 413
    assert not web.bodies


@pytest.mark.parametrize(
    "headers",
    [[], [(b"content-length", b"10")], [(b"content-length", b"lots")]],
)
def test_a_body_that_grows_past_the_limit_is_cut_off(
    headers: list[tuple[bytes, bytes]],
) -> None:
    web = _Web()
    sent = _post(web, [b"a" * 60, b"b" * 60, b"c"], headers=headers)
    assert _status(sent) == 413
    # Django sees the client go away, and its own answer goes nowhere.
    assert web.ended_by == ["http.disconnect", "http.disconnect"]
    assert sent[-1]["body"] == b"Request body too large."


def test_an_answer_already_started_is_left_alone() -> None:
    web = _Web(answer_first=True)
    sent = _post(web, [b"a" * 60, b"b" * 60])
    assert _status(sent) == 200
    assert web.ended_by == ["http.disconnect", "http.disconnect"]
    assert len(sent) == 1  # nothing after the body was refused


def test_other_connections_pass_straight_through() -> None:
    seen: list[str] = []

    async def web(scope: Any, receive: Any, send: Any) -> None:
        del receive, send
        seen.append(scope["type"])

    async def nothing(*args: Any) -> Any:
        del args

    scope = {"type": "websocket", "path": "/ws"}
    asyncio.run(asgi.Router(None, web)(scope, nothing, nothing))
    assert seen == ["websocket"]


def _cookie(role: Role | None) -> list[tuple[bytes, bytes]]:
    if role is None:
        return []
    user = User.objects.create(
        username=f"u-{role}", oidc_sub=f"s-{role}", role=role
    )
    client = Client()
    client.force_login(user, backend="accounts.oidc.Backend")
    key = client.cookies[settings.SESSION_COOKIE_NAME].value
    cookie = f"other=1; {settings.SESSION_COOKIE_NAME}={key}"
    return [(b"cookie", cookie.encode())]


@pytest.mark.parametrize(
    ("role", "status"),
    [(Role.ADMIN, 200), (Role.EDITOR, 413), (None, 413)],
)
def test_only_an_admin_may_send_a_backup_sized_body(
    role: Role | None, status: int
) -> None:
    sent = _post(_Web(), [b"a" * 250], path="/backup/", headers=_cookie(role))
    assert _status(sent) == status


def test_a_stale_session_cookie_gets_the_normal_limit() -> None:
    cookie = [(b"cookie", f"{settings.SESSION_COOKIE_NAME}=gone".encode())]
    sent = _post(_Web(), [b"a" * 250], path="/backup/", headers=cookie)
    assert _status(sent) == 413


def test_the_backup_allowance_is_only_for_the_restore_page() -> None:
    headers = _cookie(Role.ADMIN)
    assert _status(_post(_Web(), [b"a" * 250], headers=headers)) == 413
    headers += [(b"content-length", b"301")]
    sent = _post(_Web(), [b"a"], path="/backup/", headers=headers)
    assert _status(sent) == 413


def test_django_itself_is_behind_the_cap() -> None:
    async def run() -> int:
        app: Any = asgi.application
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post("/projects/new/", content=b"x" * 101)
            return response.status_code

    assert asyncio.run(run()) == 413


def test_mcp_takes_the_largest_import_and_no_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User.objects.create(username="u", oidc_sub="s", role=Role.EDITOR)
    access = AccessToken(
        token="good",
        client_id="client",
        scopes=[],
        resource=auth.resource_url(),
        claims={"user_id": user.pk},
    )
    monkeypatch.setattr(auth, "check", lambda token: access)
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "import_cues",
            "arguments": {
                "project_id": 0,
                "filename": "cues.csv",
                "content_base64": "A" * (transfers.MAX_BASE64 - 1),
            },
        },
    }
    largest = json.dumps(call).encode()
    app = asgi.build_mcp_app()

    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer good",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
    }

    async def post(*bodies: bytes) -> list[Any]:
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                return [
                    await client.post("/mcp", content=body, headers=headers)
                    for body in bodies
                ]

    fits, too_big = asyncio.run(post(largest, b" " * (asgi.MCP_BODY_MAX + 1)))
    # Read and answered by the tool (here: no such project), not refused.
    assert fits.status_code == 200
    assert "No project 0" in fits.text
    assert too_big.status_code == 413
