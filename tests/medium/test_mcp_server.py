"""The MCP server's tools and HTTP endpoint."""

import asyncio
from collections.abc import Iterator
import csv
import io
import json
import threading
import time
from typing import Any

import httpx2
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from cues.exporters.cue_sheet import generic
from cues.services import demo
from mcp_server import auth
from mcp_server import common
from mcp_server import cue_tools
from mcp_server import prompts
from mcp_server import schedule
from mcp_server import server
from mcp_server import setup
from mcp_server import transfers
from mcp_server import writes
from reellife import asgi
from tests import factories

pytestmark = pytest.mark.django_db


def _access(user: User | None) -> AccessToken:
    return AccessToken(
        token="t",
        client_id="client",
        scopes=[],
        resource=auth.resource_url(),
        subject="sub",
        claims={"user_id": user.pk if user else 0},
    )


@pytest.fixture(name="acting_as")
def fixture_acting_as() -> Iterator[Any]:
    """Sets who the tools act for, as the SDK's middleware would."""
    tokens = []

    def act(user: User | None) -> None:
        tokens.append(auth_context_var.set(AuthenticatedUser(_access(user))))

    yield act
    for token in reversed(tokens):
        auth_context_var.reset(token)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return demo.build_demo().project


def test_tools_need_a_reellife_user(acting_as: Any) -> None:
    with pytest.raises(ToolError, match="viewer role"):
        server.list_projects()
    acting_as(None)
    with pytest.raises(ToolError, match="viewer role"):
        server.list_projects()
    acting_as(factories.UserFactory.create(role=Role.NONE))
    with pytest.raises(ToolError, match="viewer role"):
        server.search("1m01")


def test_read_tools(acting_as: Any, project: models.Project) -> None:
    acting_as(factories.UserFactory.create(role=Role.VIEWER))
    found = server.search("lighthouse 1m01")
    cue = next(hit for hit in found if hit["kind"] == "cue")
    assert (cue["number"], cue["title"]) == ("1m01", "Main Title")
    assert cue["url"].startswith("http://testserver/cues/")
    assert "matched_in" not in cue
    # Notes match too, saying where.
    noted = server.search("lighthouse dialogue")
    assert noted and all(
        hit["matched_in"].startswith("Spotting Notes (v")
        and "dialogue" in hit["snippet"]
        for hit in noted
    )
    assert [p["id"] for p in server.list_projects()] == [project.pk]
    project.archived = True
    project.save()
    assert not server.list_projects()
    assert server.list_projects(include_archived=True)[0]["archived"]

    details = server.get_project(project.pk)
    assert details["frame_rate"] == project.rate.label
    assert [reel["name"] for reel in details["reels"]] == ["1", "2", "3"]
    assert details["reels"][0]["in"]
    assert details["next_deadline"] == "" or "," in details["next_deadline"]

    cues = server.list_cues(project.pk)
    assert all(row["active"] for row in cues)
    every = server.list_cues(project.pk, include_inactive=True)
    assert len(every) >= len(cues)
    first = server.get_cue(cue["id"])
    assert first["number"] == "1m01" and first["in"] and first["versions"]
    assert any(version["current"] for version in first["versions"])
    with pytest.raises(ToolError, match="No project 0"):
        server.get_project(0)
    with pytest.raises(ToolError, match="No cue 0"):
        server.get_cue(0)


def _rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


def _over_http(
    monkeypatch: pytest.MonkeyPatch,
    user: User,
    calls: list[tuple[str, dict[str, Any] | None, dict[str, str]]],
) -> list[Any]:
    """Posts each (method, params, extra headers) to /mcp as `user`.

    The token "good" is `user`'s; any other is refused.
    """
    monkeypatch.setattr(
        auth,
        "check",
        lambda token: _access(user) if token == "good" else None,
    )
    app = asgi.build_mcp_app()
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer good",
        "MCP-Protocol-Version": "2025-11-25",
    }

    async def run() -> list[Any]:
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                return [
                    await client.post(
                        "/mcp",
                        json=_rpc(method, params),
                        headers={**headers, **extra},
                    )
                    for method, params, extra in calls
                ]

    return asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    project = demo.build_demo().project
    user = factories.UserFactory.create(role=Role.VIEWER)
    get_project = {
        "name": "get_project",
        "arguments": {"project_id": project.pk},
    }
    refused, listed, called, foreign = _over_http(
        monkeypatch,
        user,
        [
            ("tools/list", None, {"Authorization": "Bearer bad"}),
            ("tools/list", None, {}),
            ("tools/call", get_project, {}),
            ("tools/list", None, {"Host": "evil.example"}),
        ],
    )
    assert refused.status_code == 401
    assert "resource_metadata=" in refused.headers["www-authenticate"]
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    every = [
        *server.TOOLS,
        *writes.TOOLS,
        *writes.READ_TOOLS,
        *setup.TOOLS,
        *cue_tools.TOOLS,
        *cue_tools.READ_TOOLS,
        *transfers.TOOLS,
        *transfers.READ_TOOLS,
        *schedule.TOOLS,
        *schedule.READ_TOOLS,
    ]
    assert names == {fn.__name__ for fn in every}
    tools = {t["name"]: t for t in listed.json()["result"]["tools"]}
    assert tools["get_cue"]["annotations"]["readOnlyHint"]
    assert not tools["edit_cue"]["annotations"]["readOnlyHint"]
    content = called.json()["result"]["content"][0]["text"]
    assert json.loads(content)["title"] == project.title
    assert foreign.status_code in {400, 403, 421}


@pytest.mark.django_db(transaction=True)
def test_resources_and_prompts_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = demo.build_demo().project
    user = factories.UserFactory.create(role=Role.VIEWER)
    cues_uri = f"reellife://projects/{project.pk}/cues"
    whats_left = {
        "name": "whats_left",
        "arguments": {"project_id": str(project.pk)},
    }
    replies = _over_http(
        monkeypatch,
        user,
        [
            ("resources/list", None, {}),
            ("resources/templates/list", None, {}),
            ("resources/read", {"uri": cues_uri}, {}),
            ("prompts/list", None, {}),
            ("prompts/get", whats_left, {}),
        ],
    )
    resources, templates, read, listed, prompt = (
        r.json()["result"] for r in replies
    )
    assert [r["uri"] for r in resources["resources"]] == [
        "reellife://projects",
        "reellife://guide",
    ]
    assert {t["title"] for t in templates["resourceTemplates"]} == {
        "Project",
        "Cue List",
        "Cue Sheet",
    }
    assert json.loads(read["contents"][0]["text"])[0]["number"] == "1m01"
    assert {p["title"] for p in listed["prompts"]} == {
        title for _, title in prompts.PROMPTS
    }
    assert f"project {project.pk}" in prompt["messages"][0]["content"]["text"]


@pytest.mark.django_db(transaction=True)
def test_router_sends_mcp_paths_and_checks_https(
    settings: Any,
) -> None:
    seen: list[str] = []

    async def mcp_app(scope: Any, receive: Any, send: Any) -> None:
        del receive, send
        seen.append(f"mcp {scope['type']} {scope.get('path', '')}")

    async def web_app(scope: Any, receive: Any, send: Any) -> None:
        del receive, send
        seen.append(f"web {scope['path']}")

    sent: list[Any] = []

    async def send(message: Any) -> None:
        sent.append(message)

    async def receive() -> Any:
        return {"type": "http.request"}

    router = asgi.Router(mcp_app, web_app)

    def call(path: str, **scope: Any) -> None:
        asyncio.run(
            router({"type": "http", "path": path, **scope}, receive, send)
        )

    asyncio.run(router({"type": "lifespan"}, receive, send))
    call("/mcp", scheme="http")
    call("/.well-known/oauth-protected-resource/mcp", scheme="http")
    call("/projects/1/", scheme="http")
    assert seen == [
        "mcp lifespan ",
        "mcp http /mcp",
        "mcp http /.well-known/oauth-protected-resource/mcp",
        "web /projects/1/",
    ]
    settings.SECURE_SSL_REDIRECT = True
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    call("/mcp", scheme="http")
    assert sent[0]["status"] == 403
    call(
        "/mcp",
        scheme="http",
        headers=[(b"host", b"x"), (b"x-forwarded-proto", b"https")],
    )
    call("/mcp", scheme="https")
    assert seen[-2:] == ["mcp http /mcp", "mcp http /mcp"]
    settings.SECURE_PROXY_SSL_HEADER = None
    call("/mcp", scheme="http", headers=[(b"x-forwarded-proto", b"https")])
    assert len([m for m in sent if m.get("status") == 403]) == 2


def test_resources(acting_as: Any, project: models.Project) -> None:
    acting_as(factories.UserFactory.create(role=Role.VIEWER))
    pid = str(project.pk)
    assert [p["id"] for p in server.projects_resource()] == [project.pk]
    assert server.project_resource(pid)["title"] == project.title
    assert server.cues_resource(pid)[0]["number"] == "1m01"
    rows = list(csv.reader(io.StringIO(server.cue_sheet_resource(pid))))
    assert rows[0] == list(generic.HEADERS)
    assert any("1m01" in row for row in rows[1:])
    with pytest.raises(ToolError, match="No project 'abc'"):
        server.project_resource("abc")


def test_prompts_name_the_project_and_tools() -> None:
    texts = [
        prompts.spotting_notes_to_cues("7", "Open on the sea."),
        prompts.whats_left("7"),
        prompts.cue_sheet_check("7"),
    ]
    assert all("project 7" in text for text in texts)
    assert "Open on the sea." in texts[0]
    assert len(texts) == len(prompts.PROMPTS)


def test_a_slow_tool_doesnt_hold_up_others() -> None:
    """Each call runs on its own pool thread, not one shared thread."""
    finished: list[str] = []
    started = threading.Event()

    def slow() -> str:
        started.set()
        time.sleep(0.3)
        finished.append("slow")
        return "slow"

    def fast() -> str:
        finished.append("fast")
        return "fast"

    async def run() -> None:
        first = asyncio.ensure_future(common.off_loop(slow)())
        await asyncio.to_thread(started.wait, 5)
        assert await common.off_loop(fast)() == "fast"
        assert await first == "slow"

    asyncio.run(run())
    assert finished == ["fast", "slow"]
