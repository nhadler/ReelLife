"""The new cue and new version dialogs."""

from collections.abc import Callable
import json
from typing import Any

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import defaults
from cues import forms
from cues import models
from cues.services import authoring
from cues.services import demo
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db


# The dialogs load through htmx; a typed-in address gets the page.
HX = {"HX-Request": "true"}


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return demo.build_demo().project


def _cue_data(project: models.Project, **overrides: str) -> dict[str, str]:
    reel1 = project.pictures.get(name="1")
    data = {
        "number": "1m15",
        "title": "Storm Breaks",
        "picture": str(reel1.pk),
        "status": str(project.statuses.get(label="To Write").pk),
        "tc_in": "1:15:00:00",
        "tc_out": "1:16:00:00",
        "cue_type": "SCORE",
        "usage": "BI",
    }
    data.update(overrides)
    return data


def _triggers(response: Any) -> dict[str, Any]:
    raw = response.get("HX-Trigger")
    return json.loads(raw) if raw else {}


# New cue


def test_dialog_suggests_the_next_number(
    editor: Client, project: models.Project
) -> None:
    response = editor.get(
        reverse("cues:new_cue", args=[project.pk]), headers=HX
    )
    html = response.content.decode()
    assert 'value="1m15"' in html
    assert "New Cue" in html
    reel2 = project.pictures.get(name="2")
    other = editor.get(
        reverse("cues:new_cue", args=[project.pk]) + f"?picture={reel2.pk}",
        headers=HX,
    )
    assert 'value="2m14"' in other.content.decode()
    assert f'<option value="{reel2.pk}" selected>' in other.content.decode()


def test_picture_change_refreshes_the_number(
    editor: Client, project: models.Project
) -> None:
    reel3 = project.pictures.get(name="3")
    response = editor.get(
        reverse("cues:suggest_number", args=[project.pk])
        + f"?picture={reel3.pk}"
    )
    assert 'value="3m14"' in response.content.decode()
    assert "<html" not in response.content.decode()


def test_empty_project_gets_a_placeholder_picture(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    response = editor.get(
        reverse("cues:new_cue", args=[project.pk]), headers=HX
    )
    assert response.status_code == 200
    assert project.pictures.get().name == "N/A"
    assert 'value="1m01"' in response.content.decode()


def test_saving_a_cue_closes_and_reloads(
    editor: Client, project: models.Project
) -> None:
    respot = project.sub_statuses.get(label="Respot")
    response = editor.post(
        reverse("cues:new_cue", args=[project.pk]),
        {**_cue_data(project), "sub_status": str(respot.pk)},
    )
    assert response.status_code == 204
    assert _triggers(response) == {
        "cues:reload": True,
        "cues:changed": True,
        "dialog:close": True,
    }
    cue = project.cues.get(number="1m15")
    assert (cue.tc_in, cue.tc_out) == (108_000, 109_440)
    assert cue.current_version.sub_status == respot  # type: ignore[union-attr]
    change_set = models.ChangeSet.objects.get(label="New cue 1m15")
    history.undo(change_set)
    assert not project.cues.filter(number="1m15").exists()


def test_save_and_add_another(editor: Client, project: models.Project) -> None:
    response = editor.post(
        reverse("cues:new_cue", args=[project.pk]),
        {**_cue_data(project), "and_new": "1"},
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "Added 1m15." in html
    assert 'value="1m16"' in html
    assert _triggers(response) == {"cues:reload": True, "cues:changed": True}


def test_duration_fills_out(editor: Client, project: models.Project) -> None:
    editor.post(
        reverse("cues:new_cue", args=[project.pk]),
        _cue_data(project, tc_out="", duration="1:30"),
    )
    cue = project.cues.get(number="1m15")
    assert cue.tc_out == 108_000 + 90 * 24


def test_matching_out_and_duration_are_accepted(
    editor: Client, project: models.Project
) -> None:
    response = editor.post(
        reverse("cues:new_cue", args=[project.pk]),
        _cue_data(project, duration="1:00"),
    )
    assert response.status_code == 204
    assert project.cues.get(number="1m15").tc_out == 109_440


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"duration": "0:10"}, "OUT and DUR disagree; clear one of them."),
        ({"tc_in": "", "duration": "0:10"}, "Set IN to use a duration."),
        ({"duration": "1:99"}, "seconds must be &lt; 60"),
        ({"number": "1m01"}, "Another active cue already has this number."),
        ({"tc_in": "1:15:00:30"}, "frames must be &lt; 24"),
        ({"tc_out": "1:00:00:00"}, "OUT can&#x27;t be before IN."),
        ({"number": ""}, "This field is required."),
    ],
)
def test_invalid_cues_rerender_with_errors(
    editor: Client,
    project: models.Project,
    overrides: dict[str, str],
    error: str,
) -> None:
    response = editor.post(
        reverse("cues:new_cue", args=[project.pk]),
        _cue_data(project, **overrides),
    )
    assert response.status_code == 200
    assert error in response.content.decode()
    assert not project.cues.filter(title="Storm Breaks").exists()


def test_pictures_from_other_projects_are_refused(
    editor: Client, project: models.Project
) -> None:
    elsewhere = factories.PictureFactory.create()
    response = editor.post(
        reverse("cues:new_cue", args=[project.pk]),
        _cue_data(project, picture=str(elsewhere.pk)),
    )
    assert "Select a valid choice" in response.content.decode()


def test_every_new_cue_gets_v1(editor: Client, project: models.Project) -> None:
    editor.post(
        reverse("cues:new_cue", args=[project.pk]),
        _cue_data(project, temp="Zimmer", key="D minor", bpm="92"),
    )
    cue = project.cues.get(number="1m15")
    assert cue.current_version is not None
    assert cue.current_version.label == "v1"
    assert cue.current_version.written_to == cue.picture.current_version()
    assert cue.current_version.temp == "Zimmer"
    assert cue.current_version.key == "D minor"


def test_viewers_cannot_open_dialogs(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    viewer = role_client(Role.VIEWER)
    cue = project.cues.first()
    assert cue is not None
    assert (
        viewer.get(
            reverse("cues:new_cue", args=[project.pk]), headers=HX
        ).status_code
        == 403
    )
    assert (
        viewer.get(
            reverse("cues:new_version", args=[cue.pk]), headers=HX
        ).status_code
        == 403
    )


def test_project_page_offers_new_cue_to_editors(
    editor: Client,
    role_client: Callable[[Role], Client],
    project: models.Project,
) -> None:
    url = reverse("cues:project", args=[project.pk])
    assert 'id="grid-new-cue"' in editor.get(url).content.decode()
    viewer_html = role_client(Role.VIEWER).get(url).content.decode()
    assert 'id="grid-new-cue"' not in viewer_html
    assert 'id="app-dialog"' in viewer_html


# New version


def test_version_dialog_prefills(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")  # v1 on c0917; picture now c1030
    html = editor.get(
        reverse("cues:new_version", args=[cue.pk]), headers=HX
    ).content.decode()
    assert 'value="v2"' in html
    assert 'value="2:09:15:18"' in html
    current = cue.picture.current_version()
    assert current is not None
    assert f'<option value="{current.pk}" selected>c1030' in html


def test_minor_increment_setting(
    editor: Client, project: models.Project
) -> None:
    settings = models.AppSettings.load()
    settings.version_increment = "minor"
    settings.save()
    cue = project.cues.get(number="2m04")
    html = editor.get(
        reverse("cues:new_version", args=[cue.pk]), headers=HX
    ).content.decode()
    assert 'value="v1.1"' in html


def _version_data(cue: models.Cue, **overrides: str) -> dict[str, str]:
    current = cue.picture.current_version()
    assert current is not None
    data = {
        "label": "v2",
        "written_to": str(current.pk),
        "tc_in": "2:09:16:00",
        "tc_out": "2:12:03:00",
        "review_status": "needs_review",
        "change_notes": "Re-timed to c1030.",
        "update_cue": "on",
    }
    data.update(overrides)
    return data


def test_saving_a_version_makes_it_current_and_conforms(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    response = editor.post(
        reverse("cues:new_version", args=[cue.pk]), _version_data(cue)
    )
    assert response.status_code == 204
    assert _triggers(response)["dialog:close"] is True
    cue.refresh_from_db()
    assert cue.current_version is not None
    assert cue.current_version.label == "v2"
    assert cue.current_version.written_to == cue.picture.current_version()
    assert cue.tc_in == 2 * 86_400 + (9 * 60 + 16) * 24  # updated too


def test_version_without_updating_the_cue(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    before = (cue.tc_in, cue.tc_out)
    data = _version_data(cue)
    del data["update_cue"]
    editor.post(reverse("cues:new_version", args=[cue.pk]), data)
    cue.refresh_from_db()
    assert (cue.tc_in, cue.tc_out) == before
    assert cue.current_version is not None
    assert cue.current_version.label == "v2"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"label": "v1"}, "already exists"),
        ({"tc_in": "nope"}, "not a timecode"),
        ({"written_to": "999999"}, "Select a valid choice"),
        ({"tc_out": "2:00:00:00"}, "OUT can&#x27;t be before IN."),
    ],
)
def test_invalid_versions_rerender(
    editor: Client,
    project: models.Project,
    overrides: dict[str, str],
    error: str,
) -> None:
    cue = project.cues.get(number="2m04")
    response = editor.post(
        reverse("cues:new_version", args=[cue.pk]),
        _version_data(cue, **overrides),
    )
    assert response.status_code == 200
    assert error in response.content.decode()
    cue.refresh_from_db()
    assert cue.current_version is not None
    assert cue.current_version.label == "v1"


def test_version_dialog_for_a_cue_without_timecodes(editor: Client) -> None:
    cue = factories.CueFactory.create(tc_in=None, tc_out=None)
    html = editor.get(
        reverse("cues:new_version", args=[cue.pk]), headers=HX
    ).content.decode()
    assert 'value="v1"' in html


# Sub status, revision notes and themes on versions


def test_new_cue_notes(editor: Client, project: models.Project) -> None:
    editor.post(
        reverse("cues:new_cue", args=[project.pk]),
        _cue_data(project, notes="Director wants it bigger."),
    )
    version = project.cues.get(number="1m15").current_version
    assert version is not None
    assert version.notes == "Director wants it bigger."


def test_version_dialog_offers_the_cue_status(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    html = editor.get(
        reverse("cues:new_version", args=[cue.pk]), headers=HX
    ).content.decode()
    assert "Cue Status" in html
    assert f'<option value="{cue.status_id}" selected>' in html
    assert 'placeholder="2:47"' in html  # DUR shown, not filled in
    assert "Save and Open Cue Details" in html


def test_version_sets_the_cue_status(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    revisions = project.statuses.get(label="Revisions")
    editor.post(
        reverse("cues:new_version", args=[cue.pk]),
        _version_data(cue, status=str(revisions.pk)),
    )
    cue.refresh_from_db()
    assert cue.status == revisions


def test_version_duration_fills_out(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    editor.post(
        reverse("cues:new_version", args=[cue.pk]),
        _version_data(cue, tc_out="", duration="3:00"),
    )
    cue.refresh_from_db()
    assert cue.current_version is not None
    version = cue.current_version
    assert version.tc_out is not None and version.tc_in is not None
    assert version.tc_out - version.tc_in == 180 * 24


def test_version_duration_must_agree(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    response = editor.post(
        reverse("cues:new_version", args=[cue.pk]),
        _version_data(cue, duration="0:10"),
    )
    assert "OUT and DUR disagree" in response.content.decode()


def test_save_and_open_cue_details(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    response = editor.post(
        reverse("cues:new_version", args=[cue.pk]),
        {**_version_data(cue), "and_open": "1"},
    )
    assert response.status_code == 204
    assert response["HX-Redirect"] == reverse("cues:cue", args=[cue.pk])


def test_grid_toolbar_has_search_filter_and_view(
    editor: Client, project: models.Project
) -> None:
    html = editor.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert 'id="grid-search"' in html
    assert 'id="grid-filter-panel"' in html
    assert 'name="row-size" value="large"' in html


def test_dialogs_show_every_field(
    editor: Client, project: models.Project
) -> None:
    """A field the template leaves out would post blank and wipe data."""
    cue = project.cues.get(number="2m04")
    pages = [
        (
            editor.get(reverse("cues:new_cue", args=[project.pk]), headers=HX),
            forms.NewCueForm(project=project),
        ),
        (
            editor.get(reverse("cues:new_version", args=[cue.pk]), headers=HX),
            forms.NewVersionForm(cue=cue),
        ),
    ]
    for response, form in pages:
        html = response.content.decode()
        missing = [n for n in form.fields if f'name="{n}"' not in html]
        assert not missing


def test_new_versions_start_with_the_current_notes(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    assert cue.current_version is not None
    cue.current_version.notes = "Keep the horns."
    cue.current_version.revision_notes = "Shorter tail."
    cue.current_version.save()
    html = editor.get(
        reverse("cues:new_version", args=[cue.pk]), headers=HX
    ).content.decode()
    assert "Keep the horns." in html
    # Revision notes belong to their revision: the next starts blank.
    assert "Shorter tail." not in html
    editor.post(
        reverse("cues:new_version", args=[cue.pk]),
        _version_data(cue, notes="Horns out.", temp="Pärt"),
    )
    cue.refresh_from_db()
    assert cue.current_version is not None
    assert cue.current_version.notes == "Horns out."
    old = cue.versions.get(label="v1")
    assert old.notes == "Keep the horns."


def test_a_new_version_reopens_review(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    approved = project.statuses.filter(kind=models.StatusKind.APPROVED).first()
    composed = project.statuses.filter(kind=models.StatusKind.DONE).first()
    assert composed is not None
    cue.status = approved
    cue.save()
    form = editor.get(
        reverse("cues:new_version", args=[cue.pk]), headers=HX
    ).context["form"]
    assert form.initial["status"] == composed.pk
    other = project.statuses.filter(kind=models.StatusKind.TO_DO).first()
    assert other is not None
    cue.status = other
    cue.save()
    form = editor.get(
        reverse("cues:new_version", args=[cue.pk]), headers=HX
    ).context["form"]
    assert form.initial["status"] == other.pk


def test_new_versions_keep_the_themes(
    editor: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    assert cue.current_version is not None
    theme = models.ThemeOption.objects.create(
        project=project, name="Aurora", color="#10b981"
    )
    cue.current_version.themes.create(theme=theme, seconds=40)
    editor.post(reverse("cues:new_version", args=[cue.pk]), _version_data(cue))
    cue.refresh_from_db()
    assert cue.current_version is not None
    assert [
        (t.theme.name, t.seconds) for t in cue.current_version.themes.all()
    ] == [("Aurora", 40)]


def test_a_first_version_has_no_themes_to_keep() -> None:
    cue = factories.CueFactory.create()  # no version yet
    version = authoring.create_version(
        models.CueVersion(cue=cue, label="v1"), update_cue=False
    )
    assert not version.themes.exists()


def test_typed_in_dialog_addresses_open_their_page(
    editor: Client, project: models.Project
) -> None:
    response = editor.get(reverse("cues:new_cue", args=[project.pk]))
    assert response["Location"] == (
        reverse("cues:project", args=[project.pk]) + "?new=1"
    )
    cue = project.cues.get(number="2m04")
    response = editor.get(reverse("cues:new_version", args=[cue.pk]))
    assert response["Location"] == reverse("cues:cue", args=[cue.pk])


def test_new_cue_numbers_fit_the_project(
    editor: Client, project: models.Project
) -> None:
    url = reverse("cues:new_cue", args=[project.pk])
    html = editor.post(url, _cue_data(project, number="zz")).content.decode()
    assert "zz doesn&#x27;t fit this project&#x27;s Valid Cue Numbers" in html
    assert not project.cues.filter(number="zz").exists()
    project.cue_number_pattern = defaults.ANY_CUE_NUMBER_PATTERN
    project.save()
    editor.post(url, _cue_data(project, number="zz"))
    assert project.cues.filter(number="zz").exists()


def test_cue_page_renames_must_fit(
    project: models.Project,
) -> None:
    cue = project.cues.get(number="2m04")
    models.Cue.objects.filter(pk=cue.pk).update(number="zz")
    cue.refresh_from_db()
    form = forms.CueDetailsForm(
        {"number": "zz"}, cue=cue, url_for=lambda name: name, can_edit=True
    )
    form.is_valid()
    assert "number" not in form.errors
    renamed = forms.CueDetailsForm(
        {"number": "yy"}, cue=cue, url_for=lambda name: name, can_edit=True
    )
    renamed.is_valid()
    assert "doesn't fit" in renamed.errors["number"][0]
