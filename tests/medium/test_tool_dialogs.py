"""The Tools menu's dialogs: load, preview, apply."""

from collections.abc import Callable
import json

from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues import tool_forms
from cues.services import tools
from tests import factories
from tests.medium.test_cue_tools import HOUR
from tests.medium.test_cue_tools import SECOND
from tests.medium.test_cue_tools import _cue
from tests.medium.test_cue_tools import _current
from tests.medium.test_cue_tools import _reel
from tests.medium.test_cue_tools import _status

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="cues")
def fixture_cues() -> list[models.Cue]:
    project = factories.ProjectFactory.create()
    reel = _reel(project, "1")
    return [_cue(reel, f"1m0{i + 1}", i * 60) for i in range(4)]


def _url(project: models.Project, name: str) -> str:
    return reverse("cues:tool", args=[project.pk, name])


def _ids(cues: list[models.Cue]) -> str:
    return ",".join(str(cue.pk) for cue in cues)


def _get(client: Client, cues: list[models.Cue], name: str) -> str:
    project = cues[0].project
    response = client.get(_url(project, name), {"cues": _ids(cues)})
    assert response.status_code == 200
    return response.content.decode()


def _post(
    client: Client,
    cues: list[models.Cue],
    name: str,
    data: dict[str, str],
    *,
    preview: bool = False,
) -> object:
    project = cues[0].project
    payload = {"cues": _ids(cues), **data}
    if preview:
        payload["preview"] = "1"
    return client.post(_url(project, name), payload)


def _text(response: object) -> str:
    content = getattr(response, "content")  # noqa: B009
    assert isinstance(content, bytes)
    return content.decode()


def _applied(response: object) -> None:
    assert response.status_code == 204  # type: ignore[attr-defined]
    events = json.loads(response["HX-Trigger"])  # type: ignore[index]
    assert events["dialog:close"]


def test_every_tool_opens(editor: Client, cues: list[models.Cue]) -> None:
    for name, form_class in {
        "new": "New Cues",
        "versions": "Add Versions",
        "split": "Split Cue",
        "merge": "Merge Cues",
        "renumber": "Renumber Cues",
        "shift": "Shift Cues",
        "move": "Move Cues",
        "copy": "Copy Cues",
        "delete": "Delete 2 cues?",
    }.items():
        chosen = cues[:1] if name == "split" else cues[:2]
        html = _get(editor, chosen, name)
        assert form_class in html
        assert 'id="tool-preview"' in html


@pytest.mark.parametrize(
    ("name", "count", "message"),
    [
        ("split", 2, "Pick exactly one cue."),
        ("merge", 1, "Pick 2 or more cues in the grid."),
        ("delete", 0, "Pick a cue in the grid first."),
    ],
)
def test_the_wrong_selection_says_so(
    editor: Client,
    cues: list[models.Cue],
    name: str,
    count: int,
    message: str,
) -> None:
    project = cues[0].project
    html = editor.get(_url(project, name), {"cues": _ids(cues[:count])})
    assert message in html.content.decode()
    response = editor.post(_url(project, name), {"cues": _ids(cues[:count])})
    assert message in _text(response)
    assert project.cues.count() == 4


def test_bad_requests(
    editor: Client,
    cues: list[models.Cue],
    role_client: Callable[[Role], Client],
) -> None:
    project = cues[0].project
    assert editor.get(_url(project, "nope")).status_code == 404
    assert editor.get(_url(project, "delete"), {"cues": "x"}).status_code == 404
    other = factories.CueFactory.create()
    assert (
        editor.get(_url(project, "delete"), {"cues": str(other.pk)}).status_code
        == 404
    )
    many = ",".join(["1"] * 2001)
    assert (
        editor.get(_url(project, "delete"), {"cues": many}).status_code == 404
    )
    viewer = role_client(Role.VIEWER)
    assert viewer.get(_url(project, "delete")).status_code == 403


def test_new_cues(editor: Client, cues: list[models.Cue]) -> None:
    project = cues[0].project
    reel = cues[0].picture
    reel_two = _reel(project, "2")
    html = editor.get(_url(project, "new")).content.decode()
    assert 'value="1m05"' in html
    html = _text(editor.get(_url(project, "new"), {"picture": reel_two.pk}))
    assert 'value="2m01"' in html
    data = {"count": "2", "picture": str(reel.pk), "number": "1m05"}
    preview = _text(
        editor.post(_url(project, "new"), {"cues": "", "preview": "1", **data})
    )
    assert "Creates 2 cues" in preview
    response = editor.post(_url(project, "new"), {"cues": "", **data})
    _applied(response)
    assert project.cues.filter(number="1m06").exists()
    data["number"] = "1m01"
    preview = _text(
        editor.post(_url(project, "new"), {"cues": "", "preview": "1", **data})
    )
    assert "1m01 (in use)" in preview


def test_new_cues_in_a_project_without_reels(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    html = editor.get(_url(project, "new")).content.decode()
    assert "New Cues" in html


def test_add_versions(editor: Client, cues: list[models.Cue]) -> None:
    html = _get(editor, cues[:2], "versions")
    assert "Mark 2 Compositions as &quot;Composed&quot;" in html
    data = {"increment": "major", "keep_notes": "on", "mark_composed": "on"}
    preview = _text(_post(editor, cues[:2], "versions", data, preview=True))
    assert "v2" in preview
    assert "Composed" in preview
    _applied(_post(editor, cues[:2], "versions", data))
    cues[0].refresh_from_db()
    assert _current(cues[0]).label == "v2"
    assert _status(cues[0]).kind == models.StatusKind.DONE


def test_add_versions_minor_setting(
    editor: Client, cues: list[models.Cue]
) -> None:
    settings = models.AppSettings.load()
    settings.version_increment = models.AppSettings.VersionIncrement.MINOR
    settings.save()
    html = _get(editor, cues[:1], "versions")
    assert 'value="minor" required id="id_increment_1" checked' in html
    assert "Mark 1 Composition as" in html


def test_split(editor: Client, cues: list[models.Cue]) -> None:
    html = _get(editor, cues[:1], "split")
    assert 'value="1:00:15:00"' in html  # the midpoint
    assert 'value="1m01a"' in html
    data = {"at": "1:00:10:00", "number": "1m01a", "new_takes": "second"}
    preview = _text(_post(editor, cues[:1], "split", data, preview=True))
    assert "1m01a is new" in preview
    assert '<td class="tc">1m01a</td>' in preview  # cue numbers in mono
    assert '<td class="tc">1:00:10:00</td>' in preview
    data_first = {**data, "new_takes": "first"}
    preview = _text(_post(editor, cues[:1], "split", data_first, preview=True))
    assert preview.index("1m01a") < preview.index("1m01<")
    bad = _text(
        _post(editor, cues[:1], "split", {**data, "at": "x"}, preview=True)
    )
    assert "Split at:" in bad
    blank = _text(
        _post(editor, cues[:1], "split", {**data, "at": ""}, preview=True)
    )
    assert "Split at: This field is required." in blank
    _applied(_post(editor, cues[:1], "split", data))
    assert cues[0].project.cues.filter(number="1m01a").exists()


def test_split_a_cue_without_times(
    editor: Client, cues: list[models.Cue]
) -> None:
    cues[0].tc_out = None
    cues[0].save()
    html = _get(editor, cues[:1], "split")
    assert "DUR —" in html
    preview = _text(
        _post(
            editor,
            cues[:1],
            "split",
            {"at": "1:00:10:00", "number": "1m01a", "new_takes": "second"},
            preview=True,
        )
    )
    assert "needs an IN and an OUT" in preview


def test_merge(editor: Client, cues: list[models.Cue]) -> None:
    html = _get(editor, cues[1:3], "merge")
    assert 'value="1:01:00:00"' in html
    data = {
        "tc_in": "1:01:00:00",
        "tc_out": "1:02:30:00",
        "keeper": str(cues[1].pk),
        "others": "inactive",
    }
    preview = _text(_post(editor, cues[1:3], "merge", data, preview=True))
    assert "Omit, inactive" in preview
    preview = _text(
        _post(
            editor,
            cues[1:3],
            "merge",
            {**data, "others": "delete"},
            preview=True,
        )
    )
    assert "Deleted" in preview
    flipped = {**data, "tc_out": "1:00:30:00"}
    assert "OUT must be after IN" in _text(
        _post(editor, cues[1:3], "merge", flipped, preview=True)
    )
    unparsed = {**data, "tc_in": "soon"}
    assert "IN:" in _text(
        _post(editor, cues[1:3], "merge", unparsed, preview=True)
    )
    _applied(_post(editor, cues[1:3], "merge", data))
    cues[2].refresh_from_db()
    assert not cues[2].active


def test_merge_cues_without_times(
    editor: Client, cues: list[models.Cue]
) -> None:
    cues[1].tc_in = None
    cues[1].save()
    html = _get(editor, cues[1:3], "merge")
    assert "Merge Cues" in html


def test_renumber(editor: Client, cues: list[models.Cue]) -> None:
    html = _get(editor, cues[1:], "renumber")
    assert 'name="new_2" id="id_new_2" value="1m04"' in html
    data = {
        "mode": "renumber",
        "reel": "keep",
        "new_0": "1m02",
        "new_1": "1m03",
        "new_2": "1m04",
    }
    bumped = _text(_post(editor, cues[1:], "renumber", {**data, "bump": "1:1"}))
    assert 'value="1m05"' in bumped
    assert 'hx-swap-oob="innerHTML"' in bumped
    assert "Renumbers 2 cues" in bumped
    bumped = _text(
        _post(editor, cues[1:], "renumber", {**data, "bump": "0:-1"})
    )
    assert "1m01 (in use)" in bumped
    overwrite = {
        **data,
        "mode": "overwrite",
        "start": "1m10",
        "keep_suffix": "on",
    }
    preview = _text(
        _post(editor, cues[1:], "renumber", overwrite, preview=True)
    )
    assert "1m12" in preview
    no_start = {**overwrite, "start": " "}
    assert "Enter a start number." in _text(
        _post(editor, cues[1:], "renumber", no_start, preview=True)
    )
    _applied(_post(editor, cues[1:], "renumber", overwrite))
    cues[3].refresh_from_db()
    assert cues[3].number == "1m12"


def test_renumber_ignores_a_bad_bump(
    editor: Client, cues: list[models.Cue]
) -> None:
    data = {"mode": "renumber", "reel": "keep", "new_0": "1m09", "bump": "x:2"}
    _applied(_post(editor, cues[:1], "renumber", data))
    cues[0].refresh_from_db()
    assert cues[0].number == "1m09"


def test_shift(editor: Client, cues: list[models.Cue]) -> None:
    data = {"by": "offset", "offset": "2.0", "reference": "", "new_time": ""}
    preview = _text(_post(editor, cues[:2], "shift", data, preview=True))
    assert "1:00:02:00" in preview
    bad = _text(
        _post(editor, cues[:2], "shift", {**data, "offset": "x"}, preview=True)
    )
    assert "Offset: Enter an offset" in bad
    opening = {**data, "offset": "", "opening": "1"}
    assert "Enter an offset" not in _text(
        _post(editor, cues[:2], "shift", opening, preview=True)
    )
    by_in = {
        "by": "in",
        "reference": str(cues[1].pk),
        "new_time": "1:01:10:00",
        "offset": "",
    }
    preview = _text(_post(editor, cues[:2], "shift", by_in, preview=True))
    assert "1:00:10:00" in preview  # 1m01 moves by the same 10 s
    nobody = {**by_in, "reference": ""}
    assert "Pick the cue to move." in _text(
        _post(editor, cues[:2], "shift", nobody, preview=True)
    )
    no_time = {**by_in, "new_time": ""}
    assert "Enter a timecode." in _text(
        _post(editor, cues[:2], "shift", no_time, preview=True)
    )
    _applied(_post(editor, cues[:2], "shift", data))
    cues[0].refresh_from_db()
    assert cues[0].tc_in == HOUR + 2 * SECOND


def test_shift_by_seq_start(editor: Client, cues: list[models.Cue]) -> None:
    by_seq = {
        "by": "seq",
        "reference": str(cues[1].pk),
        "new_time": "0:00:10:00",
        "offset": "",
    }
    assert "1m02 has no Seq. start to move from." in _text(
        _post(editor, cues[:2], "shift", by_seq, preview=True)
    )
    cues[1].seq_start = 0
    cues[1].save()
    preview = _text(_post(editor, cues[:2], "shift", by_seq, preview=True))
    assert "Moves 2 cues by +00:00:10:00" in preview


def test_move_and_copy(editor: Client, cues: list[models.Cue]) -> None:
    project = cues[0].project
    reel_two = _reel(project, "2")
    data = {"picture": str(reel_two.pk), "versions": "current"}
    assert "Reel 2" in _get(editor, cues[:2], "move")
    preview = _text(_post(editor, cues[:2], "copy", data, preview=True))
    assert "2m01" in preview
    _applied(_post(editor, cues[:2], "copy", data))
    assert reel_two.cues.count() == 2
    preview = _text(_post(editor, cues[:2], "move", data, preview=True))
    assert "Timecodes stay" in preview
    _applied(_post(editor, cues[:2], "move", data))
    assert reel_two.cues.count() == 4


def test_delete(editor: Client, cues: list[models.Cue]) -> None:
    preview = _text(_post(editor, cues[:2], "delete", {}, preview=True))
    assert "Undo (Ctrl+Z)" in preview
    _applied(_post(editor, cues[:2], "delete", {}))
    assert cues[0].project.cues.count() == 2


def test_apply_reports_late_refusals(
    editor: Client, cues: list[models.Cue], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise tools.ToolError("Someone else got there first.")

    monkeypatch.setattr(tools, "delete", refuse)
    html = _text(_post(editor, cues[:1], "delete", {}))
    assert "Someone else got there first." in html

    def invalid(*args: object, **kwargs: object) -> None:
        raise ValidationError("Not like that.")

    monkeypatch.setattr(tools, "delete", invalid)
    assert "Not like that." in _text(_post(editor, cues[:1], "delete", {}))


def test_the_overview_has_the_tools(
    editor: Client,
    cues: list[models.Cue],
    role_client: Callable[[Role], Client],
) -> None:
    project = cues[0].project
    html = editor.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    for name in ("new", "versions", "split", "merge", "renumber", "shift"):
        assert f'data-tool="{name}"' in html
    assert _url(project, "tool") in html
    viewer = role_client(Role.VIEWER)
    html = viewer.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert "data-tool=" not in html


def test_form_guards(cues: list[models.Cue]) -> None:
    project = cues[0].project
    base = tool_forms.ToolForm(project=project, cues=cues)
    with pytest.raises(NotImplementedError):
        base.preview()
    with pytest.raises(NotImplementedError):
        base.apply(None)
    split = tool_forms.SplitForm(project=project, cues=cues[:1])
    with pytest.raises(RuntimeError):
        split.preview()
    renumber = tool_forms.RenumberForm(project=project, cues=[])
    assert renumber.selection_error() == "Pick a cue in the grid first."
    renumber.bumped(0, 1)  # unbound: nothing to move
    assert not renumber.is_bound
