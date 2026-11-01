"""The Schedule over MCP: schedule and pin_cue."""

import asyncio
from collections.abc import Iterator
import datetime

import freezegun
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from mcp_server import schedule
from mcp_server import server
from tests import factories

pytestmark = pytest.mark.django_db

MON = datetime.date(2026, 11, 2)
_DAY = datetime.timedelta(days=1)


def _as(role: str) -> object:
    user = factories.UserFactory.create(role=role)
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


@pytest.fixture(autouse=True)
def fixture_today() -> Iterator[None]:
    with freezegun.freeze_time("2026-11-02 18:00:00"):
        yield


@pytest.fixture(name="cue")
def fixture_cue() -> models.Cue:
    film = factories.ProjectFactory.create(title="Night Ferry", frame_rate="24")
    factories.DeadlineFactory.create(project=film, end_date=MON + 4 * _DAY)
    picture = factories.PictureFactory.create(project=film, name="1")
    return factories.CueFactory.create(
        project=film,
        picture=picture,
        number="1m01",
        title="Main Title",
        status=film.statuses.filter(kind=models.StatusKind.TO_DO).first(),
        tc_in=86_400,
        tc_out=86_400 + 2 * 24 * 60,
    )


@pytest.fixture(name="editor")
def fixture_editor() -> Iterator[User]:
    token = _as(Role.EDITOR)
    yield User.objects.get(role=Role.EDITOR)
    auth_context_var.reset(token)  # type: ignore[arg-type]


def test_what_to_write_today(editor: User, cue: models.Cue) -> None:
    del editor
    found = schedule.schedule()
    assert found["today"] == "2026-11-02"
    assert found["summary"].startswith("On track: 1 cue")
    day = found["days"][0]
    assert day["date"] == "2026-11-02"
    assert day["cues"][0] | {"music": "x"} == {
        "cue_id": cue.pk,
        "number": "1m01",
        "title": "Main Title",
        "project": "Night Ferry",
        "music": "x",
        "of_music": "2:00",
        "pinned": False,
        "revision": False,
        "difficulty": cue.difficulty,
    }
    week = schedule.schedule("2026-11-02", "2026-11-08")
    # Two minutes spread evenly to Friday's deadline.
    assert [d["date"] for d in week["days"]] == [
        f"2026-11-0{n}" for n in range(2, 7)
    ]


def test_bad_ranges_say_why(editor: User, cue: models.Cue) -> None:
    del editor, cue
    with pytest.raises(ToolError, match="start: use a date"):
        schedule.schedule("Monday")
    with pytest.raises(ToolError, match="end is before start"):
        schedule.schedule("2026-11-05", "2026-11-04")
    with pytest.raises(ToolError, match="at most 92 days"):
        schedule.schedule("2026-11-01", "2027-03-01")


def test_pin_and_unpin(editor: User, cue: models.Cue) -> None:
    del editor
    assert schedule.pin_cue(cue.pk, "2026-11-04") == {
        "cue": "1m01",
        "pinned_to": "2026-11-04",
    }
    assert models.SchedulePin.objects.get(cue=cue).date == MON + 2 * _DAY
    found = schedule.schedule("2026-11-04")
    assert found["days"][0]["cues"][0]["pinned"]
    label = models.ChangeSet.objects.latest("pk").label
    assert label.startswith("Schedule 1m01 on Nov 4")
    with pytest.raises(ToolError, match="today or a later day"):
        schedule.pin_cue(cue.pk, "2026-11-01")
    assert schedule.pin_cue(cue.pk) == {"cue": "1m01", "unpinned": True}
    assert schedule.pin_cue(cue.pk) == {"cue": "1m01", "unpinned": False}


def test_viewers_read_but_dont_pin(cue: models.Cue) -> None:
    token = _as(Role.VIEWER)
    try:
        assert schedule.schedule()["days"]
        with pytest.raises(ToolError, match="editor role"):
            schedule.pin_cue(cue.pk, "2026-11-04")
    finally:
        auth_context_var.reset(token)  # type: ignore[arg-type]


def test_the_tools_are_served() -> None:
    tools = asyncio.run(server.build().list_tools())
    names = {tool.name for tool in tools}
    assert {"schedule", "pin_cue"} <= names
