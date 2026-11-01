"""MCP cue tools, conform, imports and exports."""

import base64
from collections.abc import Iterator
import datetime
import io
from typing import Any

from django.core.exceptions import ValidationError
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import BlobResourceContents
from mcp_types import EmbeddedResource
import openpyxl
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from cues.exporters import pdf
from cues.exporters.cue_sheet import model as cue_sheet_model
from cues.importers import apply
from cues.services import conform
from cues.services import demo
from cues.services import tools
from cues.services import turnovers
from mcp_server import common
from mcp_server import cue_tools
from mcp_server import transfers
from tests import factories
from tests import golden

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor() -> Iterator[User]:
    user = factories.UserFactory.create(role=Role.EDITOR)
    token = auth_context_var.set(
        AuthenticatedUser(
            AccessToken(
                token="t", client_id="c", scopes=[], claims={"user_id": user.pk}
            )
        )
    )
    yield user
    auth_context_var.reset(token)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return demo.build_demo().project


def _ids(project: models.Project, *numbers: str) -> list[int]:
    return [project.cues.get(number=n).pk for n in numbers]


# The Tools menu


def test_describe_a_tool(editor: User, project: models.Project) -> None:
    del editor
    described = cue_tools.describe_cue_tool(
        project.pk, "move", _ids(project, "1m01")
    )
    picture = next(o for o in described["options"] if o["name"] == "picture")
    assert {
        "value": str(project.pictures.get(name="2").pk),
        "label": "Reel 2",
    } in (picture["choices"])
    shift = cue_tools.describe_cue_tool(
        project.pk, "shift", _ids(project, "1m01")
    )
    by = next(o for o in shift["options"] if o["name"] == "by")
    assert [c["value"] for c in by["choices"]] == ["offset", "in", "seq"]
    with pytest.raises(ToolError, match="No tool 'paint'"):
        cue_tools.describe_cue_tool(project.pk, "paint", [])


def test_preview_then_confirm(editor: User, project: models.Project) -> None:
    del editor
    ids = _ids(project, "1m01", "1m02")
    preview = cue_tools.cue_tool(project.pk, "move", ids, {"picture": "Reel 2"})
    assert preview["confirm"] and preview["preview"]["rows"]
    assert project.cues.get(number="1m01").picture.name == "1"  # not yet
    with pytest.raises(ToolError):
        cue_tools.cue_tool(project.pk, "move", ids, {"picture": "Reel 9"})
    done = cue_tools.cue_tool(
        project.pk, "move", ids, {"picture": "Reel 2"}, preview["confirm"]
    )
    assert done["done"] == "Move Cues"
    assert project.cues.get(number="1m01").picture.name == "2"
    stale = cue_tools.cue_tool(project.pk, "delete", ids, None, "wrong")
    assert "confirm" in stale  # a code from another preview only previews


def test_a_tool_refused_on_apply(
    editor: User, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    del editor
    ids = _ids(project, "1m01")
    code = cue_tools.cue_tool(project.pk, "delete", ids)["confirm"]

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise tools.ToolError("Not now.")

    monkeypatch.setattr(tools, "delete", refuse)
    with pytest.raises(ToolError, match="Not now."):
        cue_tools.cue_tool(project.pk, "delete", ids, None, code)

    def invalid(*args: Any, **kwargs: Any) -> None:
        raise ValidationError("Bad.")

    monkeypatch.setattr(tools, "delete", invalid)
    with pytest.raises(ToolError, match="Bad."):
        cue_tools.cue_tool(project.pk, "delete", ids, None, code)
    with pytest.raises(ToolError, match="Not cues of this project"):
        cue_tools.cue_tool(project.pk, "delete", [0])


# Conform


def _recut(project: models.Project) -> models.Turnover:
    reel = project.pictures.get(name="1")
    current = reel.current_version()
    assert current is not None and current.tc_in is not None
    return turnovers.create_turnover(
        project,
        label="c1201",
        received_date=datetime.date(2026, 12, 1),
        reels=[
            turnovers.ReelEntry(
                reel, "1", current.tc_in + 48, (current.tc_out or 0) + 48
            )
        ],
    )


def test_conform(editor: User, project: models.Project) -> None:
    del editor
    turnover = _recut(project)
    status = cue_tools.conform_status(project.pk)
    assert status["turnover"] == "c1201"
    assert status["moved_reels"][0]["moved_by"] == "+00:00:02:00"
    assert status["on_older_picture"]
    assert cue_tools.conform_status(project.pk, turnover.pk)["moved_reels"]
    with pytest.raises(ToolError, match="No turnover 0"):
        cue_tools.conform_status(project.pk, 0)
    with pytest.raises(ToolError, match="None of those reels has cues left"):
        cue_tools.conform_shift(project.pk, ["3"])
    preview = cue_tools.conform_shift(project.pk, ["1"])
    done = cue_tools.conform_shift(project.pk, ["1"], None, preview["confirm"])
    assert done["shifted_cues"] > 0
    with pytest.raises(ToolError, match="shifted already"):  # a retried call
        cue_tools.conform_shift(project.pk, ["1"], None, preview["confirm"])
    stale = cue_tools.conform_status(project.pk)["on_older_picture"]
    marked = cue_tools.mark_conformed(project.pk, [stale[0]["cue_id"]])
    assert marked["how"] == "in place"
    with pytest.raises(ToolError, match="None of those cues"):
        cue_tools.mark_conformed(project.pk, [0])


def test_conform_shift_refused(
    editor: User, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    del editor
    _recut(project)
    code = cue_tools.conform_shift(project.pk, ["1"])["confirm"]

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise tools.ToolError("Outside the range.")

    monkeypatch.setattr(conform, "shift_reels", refuse)
    with pytest.raises(ToolError, match="Outside the range."):
        cue_tools.conform_shift(project.pk, ["1"], None, code)


def test_conform_without_turnovers(editor: User) -> None:
    del editor
    project = factories.ProjectFactory.create()
    assert cue_tools.conform_status(project.pk)["turnover"] == ""


# Imports


def _b64(path: Any) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def test_import_preview_then_confirm(editor: User) -> None:
    del editor
    project = factories.ProjectFactory.create(frame_rate="23.976")
    file = golden.FIXTURES / "cue_list.csv"
    preview = transfers.import_cues(project.pk, file.name, _b64(file))
    assert preview["new"] > 0 and preview["read_as"]
    assert not project.cues.exists()
    done = transfers.import_cues(
        project.pk, file.name, _b64(file), confirm=preview["confirm"]
    )
    assert done["new"] == preview["new"]
    assert project.cues.count() == done["new"]


def test_import_cubase_remembers_its_start(editor: User) -> None:
    del editor
    project = factories.ProjectFactory.create(frame_rate="24")
    file = golden.FIXTURES / "cubase_marker_track_linear.xml"
    options = {"start": "0:00:00:00"}
    code = transfers.import_cues(
        project.pk, file.name, _b64(file), options=options
    )["confirm"]
    transfers.import_cues(
        project.pk, file.name, _b64(file), options=options, confirm=code
    )
    project.refresh_from_db()
    assert project.daw_start == 0


def test_import_refusals(editor: User, monkeypatch: pytest.MonkeyPatch) -> None:
    del editor
    project = factories.ProjectFactory.create(frame_rate="23.976")
    with pytest.raises(ToolError, match="valid base64"):
        transfers.import_cues(project.pk, "a.csv", "!!!")
    with pytest.raises(ToolError, match="under 10 MB"):
        transfers.import_cues(project.pk, "a.csv", "A" * (14 * 1024 * 1024))
    with pytest.raises(ToolError, match="Import from"):
        transfers.import_cues(project.pk, "a.csv", "YQ==", kind="word")
    garbage = base64.b64encode(b"SESSION NAME:\tX\n").decode()
    with pytest.raises(ToolError):
        transfers.import_cues(project.pk, "s.txt", garbage)
    file = golden.FIXTURES / "cue_list.csv"
    code = transfers.import_cues(project.pk, file.name, _b64(file))["confirm"]

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ValidationError("Nope.")

    monkeypatch.setattr(apply, "apply", refuse)
    with pytest.raises(ToolError, match="Nope."):
        transfers.import_cues(project.pk, file.name, _b64(file), confirm=code)


# Exports


def _files(result: list[Any]) -> tuple[str, bytes, str]:
    text, resource = result
    assert isinstance(resource, EmbeddedResource)
    blob = resource.resource
    assert isinstance(blob, BlobResourceContents)
    return text.text, base64.b64decode(blob.blob), str(blob.uri)


def test_cue_log_exports(
    editor: User, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    del editor
    note, body, uri = _files(transfers.export(project.pk))
    assert note.startswith("The cue log:") and uri.endswith(".xlsx")
    assert openpyxl.load_workbook(io.BytesIO(body))
    _, body, uri = _files(
        transfers.export(project.pk, file_format="csv", view="Spotting")
    )
    assert uri.endswith(".csv") and b"Cue #" in body
    monkeypatch.setattr(pdf, "render", lambda template, context: b"%PDF")
    for report in ("cue_log", "wall_chart", "version_notes"):
        _, body, uri = _files(
            transfers.export(project.pk, report=report, file_format="pdf")
        )
        assert body == b"%PDF" and uri.endswith(".pdf")
    with pytest.raises(ToolError, match="No view 'Nope'"):
        transfers.export(project.pk, view="Nope")
    with pytest.raises(ToolError, match="file_format"):
        transfers.export(project.pk, file_format="doc")
    with pytest.raises(ToolError, match="report is"):
        transfers.export(project.pk, report="poster")


def test_cue_log_in_long_play_time(
    editor: User, project: models.Project
) -> None:
    del editor
    note, body, _ = _files(
        transfers.export(
            project.pk,
            file_format="csv",
            long_play_time=True,
            start_hour=0,
            leader="0:00:08:00",
        )
    )
    assert "Long Play Time: reels end to end from 00:00:08:00" in note
    assert b"Cue #" in body
    with pytest.raises(ToolError, match="leader as a timecode"):
        transfers.export(project.pk, long_play_time=True, leader="soon")
    with pytest.raises(ToolError, match="hour 0 or 1"):
        transfers.export(project.pk, long_play_time=True, start_hour=2)
    project.picture_type = models.Project.PictureType.LONG_PLAY
    project.save()
    with pytest.raises(ToolError, match="one timeline already"):
        transfers.export(project.pk, long_play_time=True)


def test_cue_sheet_export(editor: User, project: models.Project) -> None:
    del editor
    note, body, uri = _files(transfers.cue_sheet_export(project.pk))
    assert uri.endswith(".xlsx") and body
    assert note.startswith("The cue sheet:") and "Checklist:" in note
    note, body, uri = _files(
        transfers.cue_sheet_export(project.pk, file_format="csv")
    )
    assert uri.endswith(".csv")
    with pytest.raises(ToolError, match="rapidcue or csv"):
        transfers.cue_sheet_export(project.pk, file_format="pdf")
    empty = factories.ProjectFactory.create()
    note = _files(transfers.cue_sheet_export(empty.pk))[0]
    assert "Checklist:" in note


def test_a_clean_cue_sheet_says_so(
    editor: User, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    del editor
    monkeypatch.setattr(cue_sheet_model, "checklist", lambda sheet: [])
    note = _files(transfers.cue_sheet_export(project.pk))[0]
    assert "nothing a PRO would query" in note


def test_form_data_and_errors() -> None:
    data = common.as_form_data({"a": True, "b": False, "c": [1, 2], "d": None})
    assert data.getlist("c") == ["1", "2"] and data["a"] == "on"
    assert "b" not in data and "d" not in data
    assert common.fingerprint(1, "a") == common.fingerprint(1, "a")
