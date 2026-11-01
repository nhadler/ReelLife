"""The grid endpoints: load, edit a cell, undo (7.4)."""

from collections.abc import Callable
import datetime
import json
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import Client
from django.test import override_settings
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import columns
from cues import models
from cues import timecode
from cues.api import edits
from cues.api import grid
from cues.services import demo
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return demo.build_demo().project


def _grid(client: Client, project: models.Project) -> dict[str, Any]:
    response = client.get(reverse("cues:api_grid", args=[project.pk]))
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


def _row(data: dict[str, Any], number: str) -> dict[str, Any]:
    row: dict[str, Any] = next(r for r in data["rows"] if r["number"] == number)
    return row


def _patch(
    client: Client,
    cue: models.Cue,
    field: str,
    value: Any,
    *,
    stale: bool = False,
) -> Any:
    cue.refresh_from_db()
    body = {
        "field": field,
        "value": value,
        "updated_at": "2000-01-01T00:00:00+00:00"
        if stale
        else cue.updated_at.isoformat(),
    }
    return client.patch(
        reverse("cues:api_cue", args=[cue.pk]),
        json.dumps(body),
        content_type="application/json",
    )


# Loading


def test_grid_payload(editor: Client, project: models.Project) -> None:
    data = _grid(editor, project)
    assert data["project"] == {
        "id": project.pk,
        "title": project.title,
        "rate": "23.976",
    }
    assert [v["name"] for v in data["views"]] == [
        "Standard",
        "Spotting",
        "Conform",
        "Orchestration",
        "Recording",
        "Mixing",
        "Viewer",
        "Cue Sheet",
    ]
    assert len(data["rows"]) == 40
    meta = {c["id"]: c for c in data["columns"]}
    assert meta["status"]["options"][0]["label"] == "To Write"
    assert meta["usage"]["options"][0] == {
        "value": "BI",
        "label": "Background Instrumental",
    }
    assert meta["tc_in"]["editable"] and not meta["version"]["editable"]
    assert meta["version_in"]["source"] == "version"
    assert data["settings"]["single_click_edit"] is True


def test_rows_carry_timecodes_and_conform(
    editor: Client, project: models.Project
) -> None:
    data = _grid(editor, project)
    main = _row(data, "1m01")
    assert main["tc_in"]["display"] == "1:00:20:00"
    assert main["duration"]["display"] == "0:35"
    assert main["sequence_number"] == 1
    assert main["_needs_conform"] is False
    assert main["start_offset"] is None
    stale = _row(data, "2m04")
    assert stale["_needs_conform"] is True
    assert stale["start_offset"]["display"] == "+00:00:02:12"
    # Picture versions are named 'Reel 2 - c1030'.
    assert stale["current_picture"] == "Reel 2 - c1030"
    options = data["written_to"][str(stale["_picture"]["id"])]
    assert "_written_to_options" not in stale  # sent once per reel
    assert [o["label"] for o in options] == [
        "Reel 2 - c1030",
        "Reel 2 - c0917",
    ]
    assert stale["_updated_by"] == ""  # built by the demo, not a person
    # Unwritten cues have v1 too (it holds their notes), on today's picture.
    unwritten = _row(data, "1m05")
    assert unwritten["version"] == "v1"
    assert unwritten["_needs_conform"] is False
    assert unwritten["temp"]


def test_viewers_can_read_but_not_edit(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    data = _grid(role_client(Role.VIEWER), project)
    assert not any(c["editable"] for c in data["columns"])
    # Viewers see only the Viewer view.
    assert [v["name"] for v in data["views"]] == ["Viewer"]


def test_viewers_fall_back_to_every_view_without_a_viewer_view(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    models.View.objects.filter(name="Viewer").delete()
    data = _grid(role_client(Role.VIEWER), project)
    assert len(data["views"]) == 7


def test_project_views_are_included(
    editor: Client, project: models.Project
) -> None:
    factories.ViewFactory.create(project=project, name="Mine")
    assert _grid(editor, project)["views"][-1]["name"] == "Mine"


def test_editable_columns_are_every_column_with_an_inline_editor() -> None:
    expected = {
        c.id for c in columns.COLUMNS if c.editor != columns.Editor.NONE
    }
    assert grid.EDITABLE == expected


# Editing cue fields


@pytest.mark.parametrize(
    ("field", "value", "attribute", "expected"),
    [
        ("title", "  New Title ", "title", "New Title"),
        ("number", "1m01A", "number", "1m01A"),
        ("tc_in", "1:00:21:00", "tc_in", 86_904),
        ("tc_out", "", "tc_out", None),
        ("seq_start", "0:59:50:00", "seq_start", 86_160),
        ("duration", "1:00", "tc_out", 86_880 + 1_440),
        ("usage", "VV", "usage", "VV"),
        ("usage_theme", "", "usage_theme", ""),
        ("priority", "High", "priority", "High"),
        ("cue_type", "SONG", "cue_type", "SONG"),
        ("plays_as_source", True, "plays_as_source", True),
        # Department tracking (Orchestration, Recording, Mixing).
        (
            "orchestration_status",
            "Orch Approved",
            "orchestration_status",
            "Orch Approved",
        ),
        ("recording_status", "Recorded", "recording_status", "Recorded"),
        ("mix_status", "", "mix_status", ""),
        ("score", True, "score", True),
        ("prelays", True, "prelays", True),
        ("orchestration_version", " o2 ", "orchestration_version", "o2"),
        ("mix_notes", "Less reverb", "mix_notes", "Less reverb"),
    ],
)
def test_cue_field_edits(
    editor: Client,
    project: models.Project,
    field: str,
    value: Any,
    attribute: str,
    expected: Any,
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(editor, cue, field, value)
    assert response.status_code == 200, response.json()
    cue.refresh_from_db()
    assert getattr(cue, attribute) == expected
    assert response.json()["row"]["id"] == cue.pk


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("bpm", "120.5", __import__("decimal").Decimal("120.5")),
        ("bpm", "", None),
        ("notes", None, ""),
        ("spotting_notes", "Big\\nopening", "Big\\nopening"),
        ("in_note", "On the cut", "On the cut"),
        ("temp", "Zimmer", "Zimmer"),
        ("revision_notes", "Brass out", "Brass out"),
    ],
)
def test_note_columns_edit_the_current_version(
    editor: Client,
    project: models.Project,
    field: str,
    value: Any,
    expected: Any,
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(editor, cue, field, value)
    assert response.status_code == 200, response.json()
    cue.refresh_from_db()
    assert cue.current_version is not None
    assert getattr(cue.current_version, field) == expected


def test_status_edits(editor: Client, project: models.Project) -> None:
    cue = project.cues.get(number="1m01")
    omit = project.statuses.get(label="Omit")
    assert _patch(editor, cue, "status", omit.pk).status_code == 200
    cue.refresh_from_db()
    assert cue.status == omit
    assert _patch(editor, cue, "status", None).status_code == 200
    cue.refresh_from_db()
    assert cue.status is None
    other = factories.ProjectFactory.create().statuses.first()
    assert other is not None
    response = _patch(editor, cue, "status", other.pk)
    assert response.status_code == 400
    assert response.json()["error"] == "That status isn't in this project."


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("number", " ", "A cue needs a number."),
        ("number", "1m02", "Another active cue already has this number."),
        ("number", "zz", "doesn't fit this project's Valid Cue Numbers"),
        ("tc_in", "1:00:00:24", "frames must be < 24"),
        ("tc_in", "1:00:55:00", "OUT can't be before IN."),
        ("usage", "XX", "isn't a valid choice"),
        ("mix_status", "Recorded", "isn't in this project's list"),
        ("plays_as_source", "yes", "Expected true or false."),
        ("bpm", "fast", "BPM must be a number."),
        ("title", 42, "Expected text."),
        ("duration", "9:99", "seconds must be < 60"),
        ("version", "v9", "can't be edited here"),
        ("tags", [], "can't be edited here"),
        ("nonsense", 1, "can't be edited here"),
    ],
)
def test_invalid_edits_change_nothing(
    editor: Client, project: models.Project, field: str, value: Any, error: str
) -> None:
    cue = project.cues.get(number="1m01")
    before = (cue.number, cue.title, cue.tc_in, cue.updated_at)
    response = _patch(editor, cue, field, value)
    assert response.status_code == 400
    body = response.json()
    assert error in body["error"]
    assert body["row"]["number"] == "1m01"
    cue.refresh_from_db()
    assert (cue.number, cue.title, cue.tc_in, cue.updated_at) == before


def test_a_number_that_doesnt_fit_can_stay(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    models.Cue.objects.filter(pk=cue.pk).update(number="zz")
    assert _patch(editor, cue, "number", "zz").status_code == 200
    assert _patch(editor, cue, "number", "1m01").status_code == 200


def test_duration_needs_an_in(editor: Client, project: models.Project) -> None:
    cue = factories.CueFactory.create(
        project=project,
        picture=project.pictures.get(name="1"),
        number="9m01",
        tc_in=None,
        tc_out=None,
    )
    response = _patch(editor, cue, "duration", "1:00")
    assert response.json()["error"] == "Set IN before the duration."
    cue.tc_in = 100
    cue.save()
    assert _patch(editor, cue, "duration", "").status_code == 200


def test_the_cue_sheet_title_is_editable(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(editor, cue, "cue_sheet_title", "The Crossing")
    assert response.json()["row"]["cue_sheet_title"] == "The Crossing"
    # The save brings the project header back, so it isn't fetched again.
    assert project.title in response.json()["header"]
    cue.refresh_from_db()
    assert cue.cue_sheet_title == "The Crossing"


def test_timecodes_stop_at_the_last_label(
    editor: Client, project: models.Project
) -> None:
    """A duration that pushes OUT past 99:59:59:FF is refused.

    Stored, it would break every page that shows the cue.
    """
    cue = project.cues.get(number="1m01")
    last = timecode.max_frames(project.rate)
    cue.tc_in, cue.tc_out = last - 10, last
    cue.save()
    response = _patch(editor, cue, "duration", "0:01")
    assert response.status_code == 400
    assert response.json()["error"].startswith("Timecodes end at 99:59:59")
    assert _grid(editor, project)["rows"]  # the grid still loads
    version = cue.current_version
    assert version is not None
    version.tc_out = last + 1
    with pytest.raises(ValidationError, match="Timecodes end at"):
        version.full_clean()
    picture = cue.picture.current_version()
    assert picture is not None
    picture.tc_out = last + 1
    with pytest.raises(ValidationError, match="Timecodes end at"):
        picture.full_clean()


# Editing version fields


def test_version_edit_creates_v1_on_current_picture(
    editor: Client, project: models.Project
) -> None:
    # Made directly, so without the v1 every cue normally has.
    cue = factories.CueFactory.create(
        project=project, picture=project.pictures.get(name="3"), number="3m99"
    )
    assert cue.current_version is None
    response = _patch(editor, cue, "change_notes", "First pass")
    assert response.status_code == 200
    cue.refresh_from_db()
    version = cue.current_version
    assert version is not None
    assert (version.label, version.change_notes) == ("v1", "First pass")
    assert version.written_to == cue.picture.current_version()
    assert (version.tc_in, version.tc_out) == (cue.tc_in, cue.tc_out)


@pytest.mark.parametrize(
    ("field", "value", "attribute", "expected"),
    [
        ("version_in", "2:09:13:00", "tc_in", 2 * 86_400 + (9 * 60 + 13) * 24),
        ("version_out", "", "tc_out", None),
        ("version_seq_start", "2:00:00:00", "seq_start", 172_800),
        ("review_status", "approved", "review_status", "approved"),
        ("written_to", None, "written_to", None),
    ],
)
def test_version_field_edits(
    editor: Client,
    project: models.Project,
    field: str,
    value: Any,
    attribute: str,
    expected: Any,
) -> None:
    cue = project.cues.get(number="2m04")
    assert _patch(editor, cue, field, value).status_code == 200
    cue.refresh_from_db()
    assert cue.current_version is not None
    assert getattr(cue.current_version, attribute) == expected


def test_conform_by_pointing_at_the_new_picture(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    current = cue.picture.current_version()
    assert current is not None
    response = _patch(editor, cue, "written_to", current.pk)
    assert response.json()["row"]["_needs_conform"] is False


def test_written_to_must_be_in_the_project(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    elsewhere = factories.PictureVersionFactory.create()
    response = _patch(editor, cue, "written_to", elsewhere.pk)
    assert (
        response.json()["error"]
        == "That picture version isn't in this project."
    )
    response = _patch(editor, cue, "review_status", "great")
    assert response.status_code == 400


def test_invalid_version_timecodes_are_refused(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    response = _patch(editor, cue, "version_out", "2:00:00:00")
    assert response.status_code == 400
    assert response.json()["error"] == "OUT can't be before IN."


# Concurrency, bad requests, history


def test_stale_edits_get_409_and_the_current_row(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(editor, cue, "title", "Mine", stale=True)
    assert response.status_code == 409
    assert response.json()["row"]["title"] == "Main Title"
    cue.refresh_from_db()
    assert cue.title == "Main Title"


def test_an_edit_that_lands_just_before_the_save_is_kept(
    editor: Client, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The revision is checked inside the save's transaction.

    Another worker's edit committed after the request arrived, but before
    this one takes the lock, must turn this edit into a 409, not be
    overwritten by it.
    """
    cue = project.cues.get(number="1m01")
    real_atomic = transaction.atomic
    pending = [True]

    def other_worker_first(*args: Any, **kwargs: Any) -> Any:
        if pending:
            pending.clear()
            edits.apply(models.Cue.objects.get(pk=cue.pk), "scene", "Theirs")
        return real_atomic(*args, **kwargs)

    seen = cue.updated_at.isoformat()
    monkeypatch.setattr(transaction, "atomic", other_worker_first)
    response = editor.patch(
        reverse("cues:api_cue", args=[cue.pk]),
        json.dumps({"field": "title", "value": "Mine", "updated_at": seen}),
        content_type="application/json",
    )
    monkeypatch.undo()
    assert response.status_code == 409
    assert response.json()["row"]["scene"] == "Theirs"
    cue.refresh_from_db()
    assert (cue.title, cue.scene) == ("Main Title", "Theirs")


@pytest.mark.parametrize("body", ["not json", "{}", '{"field": "title"}', "[]"])
def test_malformed_requests(
    editor: Client, project: models.Project, body: str
) -> None:
    cue = project.cues.get(number="1m01")
    response = editor.patch(
        reverse("cues:api_cue", args=[cue.pk]),
        body,
        content_type="application/json",
    )
    assert response.status_code == 400


def test_get_is_not_allowed_on_the_edit_endpoint(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    assert editor.get(reverse("cues:api_cue", args=[cue.pk])).status_code == 405


def test_edits_are_undoable_one_at_a_time(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    _patch(editor, cue, "title", "First")
    _patch(editor, cue, "title", "Second")
    url = reverse("cues:api_undo", args=[project.pk])
    response = editor.post(url)
    assert response.json() == {"undone": "Edit 1m01 Title"}
    cue.refresh_from_db()
    assert cue.title == "First"
    editor.post(url)
    cue.refresh_from_db()
    assert cue.title == "Main Title"
    nothing = editor.post(url)
    assert nothing.status_code == 409
    assert nothing.json()["error"] == "Nothing to undo."


def test_undo_refuses_when_someone_edited_after(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    first, second = role_client(Role.EDITOR), role_client(Role.ADMIN)
    cue = project.cues.get(number="1m01")
    _patch(first, cue, "title", "Mine")
    _patch(second, cue, "title", "Theirs")
    response = first.post(reverse("cues:api_undo", args=[project.pk]))
    assert response.status_code == 409
    assert "changed afterwards" in response.json()["error"]


def test_header_fragment(editor: Client, project: models.Project) -> None:
    response = editor.get(reverse("cues:project_header", args=[project.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Composed" in html and "<html" not in html


@override_settings(RATELIMIT_API_WRITES="2/m")
def test_api_writes_are_rate_limited(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    assert _patch(editor, cue, "title", "A").status_code == 200
    assert _patch(editor, cue, "title", "B").status_code == 200
    assert _patch(editor, cue, "title", "C").status_code == 429


def test_edit_error_has_a_message() -> None:
    assert str(edits.EditError("x")) == "x"


@pytest.mark.parametrize(
    ("zone", "shown"),
    [("UTC", "31 Oct 00:31"), ("America/Los_Angeles", "30 Oct 17:31")],
)
def test_updated_time_is_shown_in_the_configured_zone(
    editor: Client, project: models.Project, zone: str, shown: str
) -> None:
    cue = project.cues.get(number="1m01")
    stamp = datetime.datetime(2026, 10, 31, 0, 31, tzinfo=datetime.UTC)
    models.Cue.objects.filter(pk=cue.pk).update(updated_at=stamp)
    with override_settings(TIME_ZONE=zone):
        data = editor.get(reverse("cues:api_grid", args=[project.pk])).json()
    row = next(r for r in data["rows"] if r["number"] == "1m01")
    assert row["updated_at"] == shown


def test_department_columns_carry_their_colours(
    editor: Client, project: models.Project
) -> None:
    data = _grid(editor, project)
    meta = {c["id"]: c for c in data["columns"]}
    options = meta["orchestration_status"]["options"]
    assert options[0] == {"value": "", "label": ""}
    assert options[1] == {
        "value": "To Orchestrate",
        "label": "To Orchestrate",
        "color": "#e5e7eb",
    }
    assert meta["score"]["editor"] == "checkbox"
    row = _row(data, "1m01")
    assert (row["mix_status"], row["clicks"], row["recording_notes"]) == (
        "",
        False,
        "",
    )
    status = meta["status"]["options"][0]
    assert (status["kind"], status["category"]) == ("to_do", "unapproved")


def test_pictures_carry_their_current_turnover(
    editor: Client, project: models.Project
) -> None:
    reels = _grid(editor, project)["pictures"]
    assert [r["name"] for r in reels] == ["1", "2", "3"]
    first = reels[0]
    picture = project.pictures.get(name="1")
    current = picture.current_version()
    assert current is not None
    assert first["label"] == current.turnover.label
    assert (first["tc_in"], first["tc_out"]) == (current.tc_in, current.tc_out)
    picture.archived = True
    picture.save()
    assert [r["name"] for r in _grid(editor, project)["pictures"]] == ["2", "3"]


def test_a_reel_without_picture_has_no_turnover(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    factories.PictureFactory.create(project=project, name="9")
    (reel,) = _grid(editor, project)["pictures"]
    assert (reel["label"], reel["notes"], reel["tc_in"]) == ("", "", None)


def test_lane_edits(editor: Client, project: models.Project) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(editor, cue, "lane", 2)
    assert response.status_code == 200
    assert response.json()["row"]["_lane"] == 2
    for bad, error in ((3, "Lanes run from 1 to 2."), ("1", "Expected a lane")):
        response = _patch(editor, cue, "lane", bad)
        assert response.status_code == 400
        assert error in response.json()["error"]
    cue.refresh_from_db()
    assert cue.lane == 2


def test_rows_say_who_changed_them_last(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(editor, cue, "title", "Renamed")
    user = response.wsgi_request.user
    assert response.json()["row"]["_updated_by"] == str(user)
    assert _row(_grid(editor, project), "1m01")["_updated_by"] == str(user)


def test_typing_a_new_sub_status_adds_it(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(editor, cue, "sub_status", "Needs Mix")
    assert response.status_code == 200, response.json()
    added = project.sub_statuses.get(label="Needs Mix")
    assert response.json()["row"]["sub_status"] == added.pk
    assert added.order == project.sub_statuses.count() - 1
    # The same name in other case is the same entry.
    response = _patch(editor, cue, "sub_status", "needs mix")
    assert response.json()["row"]["sub_status"] == added.pk
    assert project.sub_statuses.filter(label__iexact="needs mix").count() == 1
    response = _patch(editor, cue, "sub_status", added.pk)
    assert response.json()["row"]["sub_status"] == added.pk
    response = _patch(editor, cue, "sub_status", None)
    assert response.json()["row"]["sub_status"] is None
    meta = {c["id"]: c for c in _grid(editor, project)["columns"]}
    assert meta["sub_status"]["creatable"] is True
    assert "Needs Mix" in [o["label"] for o in meta["sub_status"]["options"]]


@pytest.mark.parametrize(
    ("value", "error"),
    [("x" * 51, "at most 50 characters"), (999_999, "isn't in this project")],
)
def test_bad_sub_statuses_are_refused(
    editor: Client, project: models.Project, value: Any, error: str
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(editor, cue, "sub_status", value)
    assert response.status_code == 400
    assert error in response.json()["error"]


# Theme and Instrumentation chips


def test_theme_chips_add_reorder_time_and_remove(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(
        editor,
        cue,
        "themes",
        [{"name": "Aurora", "seconds": 80}, {"name": "The Sea"}],
    )
    assert response.status_code == 200, response.json()
    chips = response.json()["row"]["themes"]
    assert [(c["name"], c["seconds"]) for c in chips] == [
        ("Aurora", 80),
        ("The Sea", None),
    ]
    aurora, sea = (project.themes.get(name=n) for n in ("Aurora", "The Sea"))
    # Reorder, time the second, name an existing one in other case.
    response = _patch(
        editor,
        cue,
        "themes",
        [{"id": sea.pk, "seconds": 30}, {"name": "aurora"}, {"id": sea.pk}],
    )
    chips = response.json()["row"]["themes"]
    assert [(c["id"], c["seconds"]) for c in chips] == [
        (sea.pk, 30),
        (aurora.pk, None),
    ]
    assert project.themes.count() == 2
    # Saving the same list again changes nothing.
    links = set(models.VersionTheme.objects.values_list("pk", flat=True))
    response = _patch(
        editor,
        cue,
        "themes",
        [{"id": sea.pk, "seconds": 30}, {"id": aurora.pk}],
    )
    assert response.status_code == 200
    assert (
        set(models.VersionTheme.objects.values_list("pk", flat=True)) == links
    )
    response = _patch(editor, cue, "themes", [])
    assert response.json()["row"]["themes"] == []
    # Undo the removal: both come back, in order.
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    cue.refresh_from_db()
    assert cue.current_version is not None
    assert [t.theme.name for t in cue.current_version.themes.all()] == [
        "The Sea",
        "Aurora",
    ]


def test_instrumentation_chips_are_the_cues(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(
        editor, cue, "instrumentation", [{"name": "Strings", "seconds": 100}]
    )
    assert response.json()["row"]["instrumentation"][0]["name"] == "Strings"
    assert cue.instrumentation.get().instrument.name == "Strings"
    meta = {c["id"]: c for c in _grid(editor, project)["columns"]}
    assert meta["instrumentation"]["creatable"] is True
    assert [o["label"] for o in meta["instrumentation"]["options"]] == [
        "Strings"
    ]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("Aurora", "Expected a list"),
        ([{"seconds": 3}], "needs an id or a name"),
        (["Aurora"], "needs an id or a name"),
        ([{"name": "A", "seconds": -1}], "number of seconds"),
        ([{"name": "A", "seconds": "1:20"}], "number of seconds"),
        ([{"name": "A", "seconds": True}], "number of seconds"),
        ([{"id": 999_999}], "isn't in this project"),
        ([{"name": "x" * 51}], "at most 50 characters"),
        ([{"name": f"T{n}"} for n in range(41)], "at most 40"),
    ],
)
def test_bad_chip_lists_are_refused(
    editor: Client, project: models.Project, value: Any, error: str
) -> None:
    cue = project.cues.get(number="1m01")
    response = _patch(editor, cue, "themes", value)
    assert response.status_code == 400
    assert error in response.json()["error"]
    assert not project.themes.exists()
