"""The ASGI router in front of Django and the MCP server (reellife/asgi.py).

Rate limits for /mcp (4.2): every request counts against
its client address; a token that verifies also counts against its user
and client.
"""

import asyncio
from collections.abc import Iterator
import time
from typing import Any

from django.core.cache import cache
from mcp.server.auth.provider import AccessToken
import pytest

from mcp_server import auth
from reellife import asgi

pytestmark = pytest.mark.django_db(transaction=True)


def _access(subject: str, client_id: str = "claude") -> AccessToken:
    return AccessToken(
        token="t", client_id=client_id, scopes=[], subject=subject
    )


class _Server:
    """The router with a stand-in MCP app; records what each call got."""

    def __init__(self, tokens: dict[str, AccessToken]) -> None:
        self.tokens = tokens
        self.checked: list[str] = []
        self.statuses: list[int] = []
        self.seen_by_sdk: list[AccessToken | None] = []
        self.router = asgi.Router(self._mcp_app, None)

    def check(self, token: str) -> AccessToken | None:
        self.checked.append(token)
        return self.tokens.get(token)

    async def _mcp_app(self, scope: Any, receive: Any, send: Any) -> None:
        del receive, send
        # As the SDK would, verify the token it is sent.
        header = dict(scope["headers"]).get(b"authorization", b"")
        token = header.decode()[7:]
        verifier = auth.TokenVerifier()
        self.seen_by_sdk.append(await verifier.verify_token(token))
        self.statuses.append(200)

    def call(
        self,
        authorization: bytes | None = None,
        address: str = "10.0.0.1",
        forwarded: bytes | None = None,
    ) -> int:
        headers = []
        if authorization is not None:
            headers.append((b"authorization", authorization))
        if forwarded is not None:
            headers.append((b"x-forwarded-for", forwarded))
        scope = {
            "type": "http",
            "path": "/mcp",
            "scheme": "https",
            "headers": headers,
            "client": (address, 5000),
        }
        before = len(self.statuses)

        async def send(message: Any) -> None:
            if message["type"] == "http.response.start":
                self.statuses.append(message["status"])

        async def receive() -> Any:
            return {"type": "http.request"}

        asyncio.run(self.router(scope, receive, send))
        assert len(self.statuses) == before + 1
        return self.statuses[-1]


@pytest.fixture(name="server")
def fixture_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Server]:
    cache.clear()
    server = _Server({"good": _access("ana"), "other": _access("ben")})
    monkeypatch.setattr(auth, "check", server.check)
    yield server
    cache.clear()


def test_a_user_gets_so_many_requests_a_minute(
    server: _Server, settings: Any
) -> None:
    settings.RATELIMIT_MCP_PER_MINUTE = 2
    assert [server.call(b"Bearer good") for _ in range(3)] == [200, 200, 429]
    # Another user, from the same address, has their own count.
    assert server.call(b"Bearer other") == 200


def test_a_user_is_one_count_however_the_token_is_spelled(
    server: _Server, settings: Any
) -> None:
    settings.RATELIMIT_MCP_PER_MINUTE = 2
    server.tokens["renewed"] = _access("ana")
    assert server.call(b"Bearer good") == 200
    assert server.call(b"bearer   good ") == 200
    assert server.call(b"BEARER renewed") == 429
    # The same user through another client counts separately.
    server.tokens["desktop"] = _access("ana", client_id="desktop")
    assert server.call(b"Bearer desktop") == 200


def test_made_up_tokens_count_against_the_address(
    server: _Server, settings: Any
) -> None:
    settings.RATELIMIT_MCP_PER_ADDRESS_PER_MINUTE = 3
    calls = [server.call(f"Bearer junk-{n}".encode()) for n in range(4)]
    assert calls == [200, 200, 200, 429]
    # Over the limit, the token isn't even checked.
    assert server.checked == ["junk-0", "junk-1", "junk-2"]
    assert server.call(None, address="10.0.0.2") == 200


def test_the_address_ignores_forwarded_for_unless_trusted(
    server: _Server, settings: Any
) -> None:
    settings.RATELIMIT_MCP_PER_ADDRESS_PER_MINUTE = 1
    assert server.call(forwarded=b"1.1.1.1") == 200
    assert server.call(forwarded=b"2.2.2.2") == 429
    settings.TRUST_PROXY = True
    # Behind a trusted proxy, the address it appended is the client's.
    assert server.call(forwarded=b"6.6.6.6, 3.3.3.3") == 200
    assert server.call(forwarded=b"7.7.7.7, 3.3.3.3") == 429
    assert server.call(forwarded=b"4.4.4.4") == 200


def test_each_request_checks_its_token_once(server: _Server) -> None:
    assert server.call(b"Bearer good") == 200
    assert server.call(b"Bearer good") == 200
    assert server.checked == ["good", "good"]
    assert server.seen_by_sdk == [server.tokens["good"]] * 2
    # Without a router in front, the SDK's verifier checks for itself.
    with auth.one_request():
        assert asyncio.run(auth.TokenVerifier().verify_token("other"))
    assert server.checked[-1] == "other"


def test_a_count_is_kept_per_minute(
    server: _Server, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RATELIMIT_MCP_PER_ADDRESS_PER_MINUTE = 1
    # 50 s into the next minute: near the real time, which the cache's
    # own expiry uses.
    clock = {"now": (time.time() // 60 + 1) * 60 + 50}
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    assert server.call() == 200
    assert server.call() == 429
    assert server.statuses[-1] == 429
    clock["now"] += 10  # the next minute
    assert server.call() == 200


def test_too_many_says_when_to_retry(
    server: _Server, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RATELIMIT_MCP_PER_ADDRESS_PER_MINUTE = 0
    monkeypatch.setattr(time, "time", lambda: 6_000_000.0 + 45)
    sent: list[Any] = []

    async def send(message: Any) -> None:
        sent.append(message)

    async def receive() -> Any:
        return {"type": "http.request"}

    scope = {"type": "http", "path": "/mcp", "scheme": "https", "headers": []}
    asyncio.run(server.router(scope, receive, send))
    assert sent[0]["status"] == 429
    assert (b"retry-after", b"15") in sent[0]["headers"]
