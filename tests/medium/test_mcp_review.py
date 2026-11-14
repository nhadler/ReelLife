"""Review comments over MCP: reading, replying and resolving."""

from collections.abc import Iterator

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import review
from mcp_server import review as tools
from tests import factories

pytestmark = pytest.mark.django_db

FILE_LINK = "https://www.dropbox.com/scl/fi/abc/1m01_v1.mp4?rlkey=k&dl=0"


def _as(role: str) -> object:
    user = factories.UserFactory.create(role=role, first_name="Casey")
    return auth_context_var.set(
        AuthenticatedUser(
            AccessToken(
                token="t",
                client_id="https://claude.ai/x",
                scopes=[],
                claims={"user_id": user.pk},
            )
        )
    )


@pytest.fixture(name="editor")
def fixture_editor() -> Iterator[User]:
    token = _as(Role.EDITOR)
    yield User.objects.get(role=Role.EDITOR)
    auth_context_var.reset(token)  # type: ignore[arg-type]


@pytest.fixture(name="cue")
def fixture_cue() -> models.Cue:
    film = factories.ProjectFactory.create(frame_rate="24")
    cue = factories.CueFactory.create(project=film, number="1m01")
    v1 = factories.CueVersionFactory.create(
        cue=cue, label="v1", file_link=FILE_LINK
    )
    factories.CueVersionFactory.create(cue=cue, label="v0")
    cue.current_version = v1
    cue.save()
    return cue


def test_reading_replying_and_resolving(editor: User, cue: models.Cue) -> None:
    v1 = cue.versions.get(label="v1")
    # No link and no comments yet: the current version, empty.
    assert tools.review_comments(cue.pk) == {
        "cue": "1m01",
        "link": None,
        "versions": [
            {"version": "v1", "current": True, "plays": True, "notes": []}
        ],
    }
    link = review.create(cue, None)
    review.set_password(link, "strings42")
    link.save()
    note = review.add_comment(
        v1, name="Dana", body="Too busy", frame=24, user=None
    )
    found = tools.review_comments(cue.pk)
    assert found["link"] == {
        "url": f"http://testserver/review/{link.token}/",
        "password": True,
        "last_day": "",
        "expired": False,
        "comments_open": True,
    }
    (listed,) = found["versions"][0]["notes"]
    assert listed["id"] == note.pk
    assert (listed["by"], listed["guest"], listed["body"]) == (
        "Dana",
        True,
        "Too busy",
    )
    assert (listed["at"], listed["resolved"], listed["replies"]) == (
        "1:00:11:00",
        False,
        [],
    )
    replied = tools.reply_to_comment(note.pk, "Thinned out in v2")
    assert replied["reply_to"] == note.pk
    assert replied["by"] == editor.get_full_name() == "Casey"
    reply = models.ReviewComment.objects.get(parent=note)
    assert reply.member and reply.body == "Thinned out in v2"
    assert tools.resolve_comment(note.pk) == {"id": note.pk, "resolved": True}
    (listed,) = tools.review_comments(cue.pk, "v1")["versions"][0]["notes"]
    assert listed["resolved"]
    assert [r["body"] for r in listed["replies"]] == ["Thinned out in v2"]
    assert not listed["replies"][0]["guest"]
    assert tools.resolve_comment(note.pk, resolved=False)["resolved"] is False
    # A version asked for by name is listed even when empty.
    assert tools.review_comments(cue.pk, " v0 ")["versions"] == [
        {"version": "v0", "current": False, "plays": False, "notes": []}
    ]
    link.expires_on = v1.created_at.date()
    link.save()
    assert tools.review_comments(cue.pk)["link"]["last_day"]


def test_what_cannot_be_done(editor: User, cue: models.Cue) -> None:
    del editor
    note = review.add_comment(
        cue.versions.get(label="v1"),
        name="Dana",
        body="Too busy",
        frame=None,
        user=None,
    )
    reply_id = tools.reply_to_comment(note.pk, "On it")["id"]
    with pytest.raises(ToolError, match="no version v9"):
        tools.review_comments(cue.pk, "v9")
    with pytest.raises(ToolError, match="no comment 999999"):
        tools.resolve_comment(999999)
    with pytest.raises(ToolError, match="Resolve the comment this replies to"):
        tools.resolve_comment(reply_id)
    with pytest.raises(ToolError, match="can't be replied to"):
        tools.reply_to_comment(reply_id, "Again")
    with pytest.raises(ToolError, match="Enter a comment"):
        tools.reply_to_comment(note.pk, "  ")


def test_viewers_read_but_dont_reply_or_resolve(cue: models.Cue) -> None:
    note = review.add_comment(
        cue.versions.get(label="v1"),
        name="Dana",
        body="Too busy",
        frame=None,
        user=None,
    )
    token = _as(Role.VIEWER)
    try:
        assert tools.review_comments(cue.pk)["versions"][0]["notes"]
        with pytest.raises(ToolError, match="editor role"):
            tools.resolve_comment(note.pk)
        with pytest.raises(ToolError, match="editor role"):
            tools.reply_to_comment(note.pk, "Hi")
    finally:
        auth_context_var.reset(token)  # type: ignore[arg-type]
