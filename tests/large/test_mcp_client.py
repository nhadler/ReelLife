"""A real MCP client against the running server.

gunicorn serves reeltime.asgi with its ASGI worker on a fresh database;
the official SDK's client signs in with an access token from the fake
OIDC provider (as PocketID issues one for ReelTime's /mcp) and works
through the tools (create a project, add a cue, read it back, undo),
a resource and a prompt.
"""

import asyncio
from collections.abc import Iterator
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.request
from wsgiref import simple_server

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
import pytest

from tests.fakes import oidc_provider

pytestmark = pytest.mark.large

ROOT = pathlib.Path(__file__).resolve().parents[2]
# reeltime.settings.dev's BASE_URL; tokens are issued for its /mcp.
RESOURCE = "http://127.0.0.1:8000/mcp"


class _Quiet(simple_server.WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        del format, args


@pytest.fixture(name="provider", scope="module")
def fixture_provider() -> Iterator[oidc_provider.FakeProvider]:
    server = simple_server.make_server(
        "127.0.0.1", 0, lambda e, s: [], handler_class=_Quiet
    )
    provider = oidc_provider.FakeProvider(
        f"http://127.0.0.1:{server.server_port}", "reeltime", "secret"
    )
    server.set_app(oidc_provider.wsgi_app(provider))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield provider
    server.shutdown()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(name="mcp_url", scope="module")
def fixture_mcp_url(
    provider: oidc_provider.FakeProvider, tmp_path_factory: Any
) -> Iterator[str]:
    """gunicorn's ASGI worker on a fresh database; yields /mcp's URL."""
    data = tmp_path_factory.mktemp("data")
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "reeltime.settings.dev",
        "REELTIME_DATA_DIR": str(data),
        "OIDC_ISSUER": provider.issuer,
    }
    for command in (["migrate", "--noinput"], ["createcachetable"]):
        subprocess.run(  # noqa: S603
            [sys.executable, "manage.py", *command],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
        )
    port = _free_port()
    command = [
        sys.executable,
        "-m",
        "gunicorn",
        "--worker-class",
        "asgi",
        "--workers",
        "1",
        "--bind",
        f"127.0.0.1:{port}",
        "reeltime.asgi:application",
    ]
    base = f"http://127.0.0.1:{port}"
    with subprocess.Popen(  # noqa: S603
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as server:
        try:
            _wait_until_up(f"{base}/healthz")
            yield f"{base}/mcp"
        finally:
            server.terminate()


def _wait_until_up(url: str) -> None:
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen(url, timeout=1):  # noqa: S310
                return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


async def _session_calls(url: str, token: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()

                async def call(name: str, **arguments: Any) -> Any:
                    """The tool's reply: one JSON value per text block."""
                    result = await session.call_tool(name, arguments)
                    assert not result.is_error, result.content
                    values = [
                        json.loads(block.text)  # type: ignore[union-attr]
                        for block in result.content
                    ]
                    return values[0] if len(values) == 1 else values

                project = await call(
                    "create_project", title="Night Ferry", frame_rate="24"
                )
                await call(
                    "log_turnover",
                    project_id=project["id"],
                    label="LOCK",
                    received_date="2026-10-01",
                    reels=[
                        {"name": "1", "in": "01:00:00:00", "out": "01:20:00:00"}
                    ],
                )
                cue = await call(
                    "new_cue",
                    project_id=project["id"],
                    number="1m01",
                    title="Open Water",
                    reel="1",
                    tc_in="01:00:10:00",
                    tc_out="01:01:00:00",
                )
                found = await call("search", query="open water")
                await call(
                    "edit_cue",
                    cue_id=cue["created"]["id"],
                    field="title",
                    value="Open Sea",
                )
                undone = await call(
                    "undo_last_change", project_id=project["id"]
                )
                after = await call("get_cue", cue_id=cue["created"]["id"])
                sheet = await session.read_resource(
                    f"reeltime://projects/{project['id']}/cue-sheet"
                )
                prompt = await session.get_prompt(
                    "whats_left", {"project_id": str(project["id"])}
                )
                return {
                    "tools": {tool.name for tool in tools.tools},
                    "found": found,
                    "undone": undone,
                    "title": after["title"],
                    "sheet": sheet.contents[0].text,  # type: ignore[union-attr]
                    "prompt": prompt.messages[0].content.text,  # type: ignore[union-attr]
                }


def test_a_real_client_works_through_the_tools(
    provider: oidc_provider.FakeProvider, mcp_url: str
) -> None:
    user = oidc_provider.DEV_USERS["composer"]
    token = provider.resource_token(user, RESOURCE)
    seen = asyncio.run(_session_calls(mcp_url, token))
    assert {"create_project", "new_cue", "import_cues", "export"} <= seen[
        "tools"
    ]
    assert seen["found"]["number"] == "1m01"
    assert seen["undone"]["undone"].startswith("Edit 1m01 Title (via MCP,")
    assert seen["title"] == "Open Water"
    assert "Open Water" in seen["sheet"]
    assert "next deadline" in seen["prompt"]


def test_tokens_for_anything_else_are_refused(
    provider: oidc_provider.FakeProvider, mcp_url: str
) -> None:
    user = oidc_provider.DEV_USERS["composer"]
    for token in (
        provider.resource_token(user, "http://127.0.0.1:8000/other"),
        provider.resource_token(oidc_provider.DEV_USERS["outsider"], RESOURCE),
        "not-a-token",
    ):
        reply = httpx2.post(
            mcp_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert reply.status_code == 401
