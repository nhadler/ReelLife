"""The cue details page."""

from collections.abc import Callable
import json
from typing import Any

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues.services import authoring
from cues.services import demo
from cues.services import history
from cues.views import cue_details
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="viewer")
def fixture_viewer(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.VIEWER)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return demo.build_demo().project


def _cue(project: models.Project, number: str) -> models.Cue:
    return project.cues.get(number=number)


def _page(client: Client, cue: models.Cue, query: str = "") -> str:
    response = client.get(reverse("cues:cue", args=[cue.pk]) + query)
    assert response.status_code == 200
    return response.content.decode()


def _save(
    client: Client, cue: models.Cue, field: str, value: Any = None, **extra: Any
) -> Any:
    cue.refresh_from_db()
    data: dict[str, Any] = {"updated_at": cue.updated_at.isoformat(), **extra}
    if value is not None:
        data[field] = value
    return client.post(reverse("cues:cue_field", args=[cue.pk, field]), data)


def _save_version(
    client: Client, version: models.CueVersion, field: str, value: str
) -> Any:
    version.cue.refresh_from_db()
    return client.post(
        reverse("cues:version_field", args=[version.pk, field]),
        {
            "updated_at": version.cue.updated_at.isoformat(),
            f"version-{field}": value,
        },
    )


def _triggers(response: Any) -> dict[str, Any]:
    raw = response.get("HX-Trigger")
    return json.loads(raw) if raw else {}


# The page


def test_page_shows_cue_versions_and_fields(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "2m04")
    html = _page(editor, cue)
    assert "2m04 Keeper" in html
    assert 'id="cue-updated-at"' in html
    assert f'hx-post="/cues/{cue.pk}/fields/title/"' in html
    assert "Version v1" in html
    assert 'version-list__current">Current' in html
    assert "Needs conform." in html
    assert "Start Offset" in html
    assert 'id="new-version"' in html
    assert "Delete cue" in html
    # The version's fields are prefixed: they don't clash with the cue's.
    assert 'id="id_version-tc_in"' in html
    assert 'id="id_tc_in"' in html


def test_viewers_see_read_only_fields(
    viewer: Client, project: models.Project
) -> None:
    cue = _cue(project, "2m04")
    html = _page(viewer, cue)
    assert "hx-post" not in html
    # Read as label and value; nothing to type into.
    assert 'name="title"' not in html
    assert '<span class="field__label">Cue #</span>' in html
    assert f'<p class="field__value">{cue.number}</p>' in html
    assert "Delete cue" not in html
    assert 'id="new-version"' not in html


def test_choosing_a_version(editor: Client) -> None:
    cue = factories.CueFactory.create()
    first = factories.CueVersionFactory.create(cue=cue, label="v1")
    second = factories.CueVersionFactory.create(cue=cue, label="v2")
    cue.current_version = second
    cue.save()
    html = _page(editor, cue, f"?version={first.pk}")
    assert "Version v1" in html
    assert "Make v1 Current" in html
    other = factories.CueVersionFactory.create()
    response = editor.get(
        reverse("cues:cue", args=[cue.pk]) + f"?version={other.pk}"
    )
    assert response.status_code == 404


def test_newest_version_shows_when_none_is_current(editor: Client) -> None:
    cue = factories.CueFactory.create()
    factories.CueVersionFactory.create(cue=cue, label="v1")
    factories.CueVersionFactory.create(cue=cue, label="v2")
    assert "Version v2" in _page(editor, cue)


def test_cue_without_versions(editor: Client) -> None:
    html = _page(editor, factories.CueFactory.create())
    assert "No versions yet." in html
    assert "No version yet, so there's nothing to compare." in html
    assert "Delete Version" not in html


@pytest.mark.parametrize(
    ("link", "linked"),
    [
        ("https://drive.example.com/1m01.wav", True),
        ("http://nas.local/1m01.wav", True),
        ("javascript:alert(1)", False),
        ("/Volumes/Mixes/1m01.wav", False),
        ("https://", False),
    ],
)
def test_only_web_links_are_clickable(
    editor: Client, link: str, linked: bool
) -> None:
    version = factories.CueVersionFactory.create(file_link=link)
    html = _page(editor, version.cue)
    assert ("Open the Bounce" in html) is linked
    assert 'href="javascript' not in html


def test_previous_and_next_follow_the_grid_order(
    editor: Client, project: models.Project
) -> None:
    first = _cue(project, "1m01")
    html = _page(editor, first)
    assert 'id="cue-previous"' not in html
    second = _cue(project, "1m02")
    assert f'id="cue-next" href="/cues/{second.pk}/"' in html
    html = _page(editor, second)
    assert f'id="cue-previous" href="/cues/{first.pk}/"' in html


def test_switcher_lists_every_cue_by_reel(
    editor: Client, project: models.Project
) -> None:
    inactive = _cue(project, "3m01")
    inactive.active = False
    inactive.save()
    html = _page(editor, _cue(project, "1m01"))
    assert "Reel 3" in html
    assert 'class="switcher__cue--inactive"' in html
    assert f'href="/cues/{inactive.pk}/"' in html


def test_inactive_cue_is_marked(editor: Client) -> None:
    cue = factories.CueFactory.create(active=False)
    assert '<span class="chip">Inactive</span>' in _page(editor, cue)


# Saving cue fields


def test_saving_a_field(editor: Client, project: models.Project) -> None:
    cue = _cue(project, "1m01")
    response = _save(editor, cue, "title", "Main Title (alt)")
    html = response.content.decode()
    assert response.status_code == 200
    assert _triggers(response) == {"cues:changed": True}
    assert 'value="Main Title (alt)"' in html
    assert 'id="cue-updated-at"' in html
    assert 'hx-swap-oob="true"' in html
    assert "1m01 Main Title (alt)" in html  # the heading
    cue.refresh_from_db()
    assert cue.title == "Main Title (alt)"
    assert models.ChangeSet.objects.filter(label="Edit 1m01 Title").exists()


def test_stale_saves_are_refused(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m01")
    response = editor.post(
        reverse("cues:cue_field", args=[cue.pk, "title"]),
        {"updated_at": "2000-01-01T00:00:00+00:00", "title": "Clobber"},
    )
    html = response.content.decode()
    assert "changed somewhere else" in html
    assert 'value="Clobber"' in html  # kept so it isn't lost
    assert "HX-Trigger" not in response
    cue.refresh_from_db()
    assert cue.title == "Main Title"


def test_invalid_values_are_refused(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m02")
    html = _save(editor, cue, "tc_in", "1:00:00:99").content.decode()
    assert "frames must be &lt; 24" in html
    assert 'value="1:00:00:99"' in html
    html = _save(editor, cue, "number", "1m01").content.decode()
    assert "Another active cue already has this number." in html


def test_unknown_fields_are_not_found(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m01")
    assert _save(editor, cue, "project", "1").status_code == 404
    assert _save(editor, cue, "tags", "1").status_code == 404


def test_timing_saves_refresh_the_other_timing_fields(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m01")
    html = _save(editor, cue, "duration", "2:00").content.decode()
    assert 'id="f-id_tc_out" hx-swap-oob="true"' in html
    assert 'id="f-id_tc_in" hx-swap-oob="true"' in html
    cue.refresh_from_db()
    assert cue.duration == 120 * 24


def test_checkboxes(editor: Client, project: models.Project) -> None:
    cue = _cue(project, "1m01")
    _save(editor, cue, "plays_as_source", "on")
    _save(editor, cue, "active")  # unticked: not posted
    cue.refresh_from_db()
    assert cue.plays_as_source
    assert not cue.active


def test_moving_a_cue_to_another_reel(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m01")
    reel2 = project.pictures.get(name="2")
    _save(editor, cue, "picture", str(reel2.pk))
    cue.refresh_from_db()
    assert cue.picture == reel2
    elsewhere = factories.PictureFactory.create()
    html = _save(editor, cue, "picture", str(elsewhere.pk)).content.decode()
    assert "That picture isn&#x27;t in this project." in html


# Saving version fields


def test_saving_a_version_field(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "2m04")
    version = cue.current_version
    assert version is not None
    before = cue.updated_at
    response = _save_version(
        editor, version, "change_notes", "Hit the door slam."
    )
    assert response.status_code == 200
    assert _triggers(response) == {"cues:changed": True}
    version.refresh_from_db()
    assert version.change_notes == "Hit the door slam."
    cue.refresh_from_db()
    assert cue.updated_at > before


def test_renaming_a_version_refreshes_the_list(
    editor: Client, project: models.Project
) -> None:
    version = _cue(project, "2m04").current_version
    assert version is not None
    html = _save_version(editor, version, "label", "v1b").content.decode()
    assert 'id="cue-versions" hx-swap-oob="true"' in html
    assert ">v1b<" in html


def test_editing_an_older_version_leaves_the_cue_alone(editor: Client) -> None:
    cue = factories.CueFactory.create()
    old = factories.CueVersionFactory.create(cue=cue, label="v1")
    current = factories.CueVersionFactory.create(cue=cue, label="v2")
    cue.current_version = current
    cue.save()
    _save_version(editor, old, "tc_in", "1:00:05:00")
    old.refresh_from_db()
    cue.refresh_from_db()
    assert old.tc_in == 86_400 + 5 * 24
    assert cue.tc_in == 86_640


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("label", "", "A version needs a label."),
        ("label", "v2", "already exists"),
        ("tc_out", "nope", "not a timecode"),
        ("delivered_at", "soon", "Enter a date and time."),
        ("review_status", "great", "isn&#x27;t a valid choice"),
        ("written_to", "999999", "isn&#x27;t in this project"),
    ],
)
def test_invalid_version_values(
    editor: Client, field: str, value: str, error: str
) -> None:
    cue = factories.CueFactory.create()
    version = factories.CueVersionFactory.create(cue=cue, label="v1")
    factories.CueVersionFactory.create(cue=cue, label="v2")
    response = _save_version(editor, version, field, value)
    assert error in response.content.decode()
    assert "HX-Trigger" not in response


def test_version_dates_links_and_turnovers(editor: Client) -> None:
    version = factories.CueVersionFactory.create()
    turnover = factories.PictureVersionFactory.create(
        picture=version.cue.picture
    )
    _save_version(editor, version, "delivered_at", "2026-10-30T14:00")
    _save_version(editor, version, "file_link", " https://x.example/a.wav ")
    _save_version(editor, version, "written_to", str(turnover.pk))
    _save_version(editor, version, "review_status", "approved")
    version.refresh_from_db()
    assert version.delivered_at is not None
    assert version.file_link == "https://x.example/a.wav"
    assert version.written_to == turnover
    assert version.review_status == "approved"
    _save_version(editor, version, "written_to", "")
    _save_version(editor, version, "delivered_at", "")
    version.refresh_from_db()
    assert version.written_to is None
    assert version.delivered_at is None


def test_version_saves_check_for_stale_pages(editor: Client) -> None:
    version = factories.CueVersionFactory.create(change_notes="Before")
    response = editor.post(
        reverse("cues:version_field", args=[version.pk, "change_notes"]),
        {"updated_at": "stale", "version-change_notes": "After"},
    )
    assert "changed somewhere else" in response.content.decode()
    version.refresh_from_db()
    assert version.change_notes == "Before"


def test_unknown_version_fields_are_not_found(editor: Client) -> None:
    version = factories.CueVersionFactory.create()
    assert _save_version(editor, version, "cue", "1").status_code == 404


# Sub status (per version; replacing tags; Oct 31)


def test_sub_status_is_a_version_field(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m01")
    version = cue.current_version
    assert version is not None
    respot = project.sub_statuses.get(label="Respot")
    response = _save_version(editor, version, "sub_status", str(respot.pk))
    assert response.status_code == 200
    version.refresh_from_db()
    assert version.sub_status == respot
    history.undo(models.ChangeSet.objects.get(label="Edit 1m01 v1 Sub Status"))
    version.refresh_from_db()
    assert version.sub_status is None


def test_sub_status_must_be_the_projects(
    editor: Client, project: models.Project
) -> None:
    version = _cue(project, "1m01").current_version
    assert version is not None
    foreign = factories.SubStatusFactory.create()
    _save_version(editor, version, "sub_status", str(foreign.pk))
    version.refresh_from_db()
    assert version.sub_status is None


# The conform strip


def test_compare_strip(editor: Client, project: models.Project) -> None:
    on_old = _cue(project, "2m04")
    html = editor.get(
        reverse("cues:cue_compare", args=[on_old.pk])
    ).content.decode()
    assert "Needs conform." in html
    assert "+00:00:02:12" in html
    current = _cue(project, "1m01")
    html = editor.get(
        reverse("cues:cue_compare", args=[current.pk])
    ).content.decode()
    assert "was written to the current picture (c0917)" in html


def test_compare_strip_without_a_turnover(editor: Client) -> None:
    version = factories.CueVersionFactory.create(written_to=None)
    version.cue.current_version = version
    version.cue.save()
    html = _page(editor, version.cue)
    assert "isn't tied to a turnover" in html


def test_compare_strip_without_cue_timecodes(editor: Client) -> None:
    cue = factories.CueFactory.create(tc_in=None, tc_out=None)
    turnover = factories.PictureVersionFactory.create(picture=cue.picture)
    old = factories.PictureVersionFactory.create(picture=cue.picture)
    old.turnover.received_date = turnover.turnover.received_date.replace(
        year=2000
    )
    old.turnover.save()
    version = factories.CueVersionFactory.create(cue=cue, written_to=old)
    cue.current_version = version
    cue.save()
    html = _page(editor, cue)
    assert "Needs conform." in html
    assert '<dd class="tc">—</dd>' in html


# History and revert


def test_history_lists_changes(editor: Client, project: models.Project) -> None:
    cue = _cue(project, "1m01")
    _save(editor, cue, "title", "Opening")
    html = editor.get(
        reverse("cues:cue_history", args=[cue.pk])
    ).content.decode()
    assert "Edit 1m01 Title" in html
    assert "<del>Main Title</del>" in html
    assert "<ins>Opening</ins>" in html
    assert "Revert" in html
    assert "Created cue" in html


def test_viewers_see_history_without_revert(
    editor: Client, viewer: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m01")
    _save(editor, cue, "title", "Opening")
    html = viewer.get(
        reverse("cues:cue_history", args=[cue.pk])
    ).content.decode()
    assert "Edit 1m01 Title" in html
    assert "Revert" not in html


def test_reverting_a_change(editor: Client, project: models.Project) -> None:
    cue = _cue(project, "1m01")
    _save(editor, cue, "title", "Opening")
    change_set = models.ChangeSet.objects.get(label="Edit 1m01 Title")
    response = editor.post(
        reverse("cues:cue_revert", args=[cue.pk, change_set.pk])
    )
    assert response.status_code == 204
    assert response["HX-Refresh"] == "true"
    cue.refresh_from_db()
    assert cue.title == "Main Title"


def test_older_changes_wait_for_newer_ones(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m01")
    _save(editor, cue, "title", "Opening")
    _save(editor, cue, "artist", "Someone else")
    older = models.ChangeSet.objects.get(label="Edit 1m01 Title")
    html = editor.get(
        reverse("cues:cue_history", args=[cue.pk])
    ).content.decode()
    assert html.count(">\n          Revert") == 1  # only the newest
    response = editor.post(reverse("cues:cue_revert", args=[cue.pk, older.pk]))
    assert cue_details.NEWER_FIRST.split(",", maxsplit=1)[0] in (
        response.content.decode()
    )
    cue.refresh_from_db()
    assert cue.title == "Opening"


def test_reverting_twice(editor: Client, project: models.Project) -> None:
    cue = _cue(project, "1m01")
    _save(editor, cue, "title", "Opening")
    change_set = models.ChangeSet.objects.get(label="Edit 1m01 Title")
    url = reverse("cues:cue_revert", args=[cue.pk, change_set.pk])
    editor.post(url)
    response = editor.post(url)
    assert "was already undone" in response.content.decode()


def test_reverting_a_new_cue_returns_to_the_project(
    editor: Client, project: models.Project
) -> None:
    cue = authoring.create_cue(
        models.Cue(
            project=project, picture=project.pictures.first(), number="9m01"
        ),
    )
    change_set = models.ChangeSet.objects.get(label="New cue 9m01")
    response = editor.post(
        reverse("cues:cue_revert", args=[cue.pk, change_set.pk])
    )
    assert response["HX-Redirect"] == reverse("cues:project", args=[project.pk])
    assert not models.Cue.objects.filter(pk=cue.pk).exists()


def test_change_sets_from_other_projects_are_not_found(
    editor: Client, project: models.Project
) -> None:
    other = factories.ProjectFactory.create()
    change_set = models.ChangeSet.objects.create(project=other, label="x")
    url = reverse(
        "cues:cue_revert", args=[_cue(project, "1m01").pk, change_set.pk]
    )
    assert editor.post(url).status_code == 404


def test_imports_say_they_revert_every_cue(
    editor: Client, project: models.Project
) -> None:
    first, second = _cue(project, "1m01"), _cue(project, "1m02")
    with history.recording(project, "Import spot.csv"):
        for cue in (first, second):
            cue.artist = "Imported"
            cue.save()
    html = editor.get(
        reverse("cues:cue_history", args=[first.pk])
    ).content.decode()
    assert "Revert all 2 cues" in html
    assert "changed 1 other cue." in html


# Options


def test_make_current(editor: Client) -> None:
    cue = factories.CueFactory.create()
    old = factories.CueVersionFactory.create(cue=cue, label="v1")
    new = factories.CueVersionFactory.create(cue=cue, label="v2")
    cue.current_version = new
    cue.save()
    response = editor.post(reverse("cues:version_make_current", args=[old.pk]))
    assert response["HX-Refresh"] == "true"
    cue.refresh_from_db()
    assert cue.current_version == old


def test_deleting_the_current_version(editor: Client) -> None:
    cue = factories.CueFactory.create()
    old = factories.CueVersionFactory.create(cue=cue, label="v1")
    new = factories.CueVersionFactory.create(cue=cue, label="v2")
    cue.current_version = new
    cue.save()
    response = editor.post(reverse("cues:version_delete", args=[new.pk]))
    assert response["HX-Redirect"] == reverse("cues:cue", args=[cue.pk])
    cue.refresh_from_db()
    assert cue.current_version == old
    history.undo(models.ChangeSet.objects.get(label=f"Delete {cue.number} v2"))
    cue.refresh_from_db()
    assert cue.current_version == new


def test_deleting_an_older_version(editor: Client) -> None:
    cue = factories.CueFactory.create()
    old = factories.CueVersionFactory.create(cue=cue, label="v1")
    new = factories.CueVersionFactory.create(cue=cue, label="v2")
    cue.current_version = new
    cue.save()
    editor.post(reverse("cues:version_delete", args=[old.pk]))
    cue.refresh_from_db()
    assert cue.current_version == new
    assert list(cue.versions.all()) == [new]


def test_deleting_a_cue_can_be_undone(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "2m04")
    current = cue.current_version
    assert current is not None
    current.sub_status = project.sub_statuses.get(label="Respot")
    current.save()
    versions = cue.versions.count()
    response = editor.post(reverse("cues:cue_delete", args=[cue.pk]))
    assert response["HX-Redirect"] == reverse("cues:project", args=[project.pk])
    assert not models.Cue.objects.filter(pk=cue.pk).exists()
    history.undo(models.ChangeSet.objects.get(label="Delete cue 2m04"))
    restored = models.Cue.objects.get(pk=cue.pk)
    assert restored.versions.count() == versions
    assert restored.current_version is not None
    assert restored.current_version.sub_status.label == "Respot"  # type: ignore[union-attr]


# The grid's way in


def test_project_page_links_to_details(
    viewer: Client, project: models.Project
) -> None:
    html = viewer.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert 'id="grid-details"' in html
    assert '"detailUrl": "/cues/0/"' in html


def test_department_groups_show_on_the_page(
    role_client: Callable[[Role], Client],
) -> None:
    cue = factories.CueFactory.create(orchestration_status="Orchestrated")
    html = (
        role_client(Role.EDITOR)
        .get(reverse("cues:cue", args=[cue.pk]))
        .content.decode()
    )
    for group in ("Orchestration", "Recording", "Mix"):
        assert f'<a href="#group-{group.lower()}"' in html  # in the index
    # A group with something in it is open; empty ones fold shut.
    assert 'id="group-orchestration" open>' in html
    assert 'id="group-recording">' in html
    assert '<option value="Orchestrated" selected>' in html
    assert 'name="prelays"' in html
