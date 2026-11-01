"""Settings as pages: departments, views, project and app settings.

Nick, Oct 31: a page per section rather than one long page; department
status lists editable per project; duplicate, archive and delete; and a
page for the app-wide settings.
"""

from collections.abc import Callable
import datetime
from decimal import Decimal
from html import unescape
import pathlib
from typing import Any

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues.services import history
from cues.services import project_admin
from cues.services import thumbnails
from cues.services import view_presets
from tests import factories

pytestmark = pytest.mark.django_db

SECTIONS = (
    "settings",
    "settings_picture",
    "settings_cue_sheet",
    "settings_statuses",
    "settings_lists",
    "settings_departments",
    "settings_deadlines",
    "settings_views",
    "settings_project",
)


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return factories.ProjectFactory.create(title="The Voyage")


def _url(name: str, project: models.Project) -> str:
    return reverse(f"cues:{name}", args=[project.pk])


def _management(prefix: str, total: int, initial: int) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def test_every_page_has_the_sidebar(
    editor: Client, project: models.Project
) -> None:
    for name in SECTIONS:
        html = editor.get(_url(name, project)).content.decode()
        assert 'class="settings__nav"' in html, name
        assert html.count('aria-current="page"') >= 1
        for other in SECTIONS:
            assert _url(other, project) in html


# Department lists -----------------------------------------------------------


def _department_data(
    project: models.Project,
    changes: dict[str, str] | None = None,
    *,
    add_to: str = "",
    field: str = "mix_status",
) -> dict[str, str]:
    """One department tab's form data, with a new row when `add_to`."""
    options = list(project.department_statuses.filter(field=field))
    extra = 1 if add_to == field else 0
    data = _management(field, len(options) + extra, len(options))
    for i, option in enumerate(options):
        data |= {
            f"{field}-{i}-id": str(option.pk),
            f"{field}-{i}-project": str(project.pk),
            f"{field}-{i}-name": option.name,
            f"{field}-{i}-color": option.color,
            f"{field}-{i}-order": str(option.order),
        }
    return data | (changes or {})


def test_projects_start_with_the_department_lists(
    project: models.Project,
) -> None:
    mix = project.department_statuses.filter(field="mix_status")
    assert mix.first() is not None
    assert mix.count() == len(models.DEPARTMENT_STATUSES["mix_status"])


def test_rename_add_and_refuse_deleting_in_use(
    editor: Client, project: models.Project
) -> None:
    cue = factories.CueFactory.create(project=project, mix_status="To Mix")
    url = _url("settings_departments", project) + "?list=mix_status"
    html = editor.get(url).content.decode()
    # The tab names the list; no heading repeats it.
    assert 'href="?list=mix_status" aria-current="page">' in html
    tab = editor.get(_url("settings_departments", project) + "?list=nope")
    page = tab.content.decode()
    assert 'href="?list=orchestration_status" aria-current="page">' in page
    mix = list(project.department_statuses.filter(field="mix_status"))
    first = mix.index(next(o for o in mix if o.name == "To Mix"))
    count = len(mix)
    data = _department_data(
        project,
        {
            f"mix_status-{first}-name": "Waiting",
            f"mix_status-{count}-name": "Printed",
            f"mix_status-{count}-color": "#336699",
            f"mix_status-{count}-order": "99",
        },
        add_to="mix_status",
    )
    assert editor.post(url, data)["Location"] == url
    cue.refresh_from_db()
    assert cue.mix_status == "Waiting"
    assert project.department_statuses.filter(
        field="mix_status", name="Printed"
    ).exists()
    change_set = models.ChangeSet.objects.get(project=project)
    assert change_set.label == (
        "Edit Mix Status: add Printed; rename To Mix to Waiting"
    )
    data = _department_data(project, {f"mix_status-{first}-DELETE": "on"})
    html = editor.post(url, data).content.decode()
    assert "Waiting is used by 1 cue" in html
    assert project.department_statuses.filter(name="Waiting").exists()
    # An unused one goes.
    mix = list(project.department_statuses.filter(field="mix_status"))
    printed = mix.index(next(o for o in mix if o.name == "Printed"))
    data = _department_data(project, {f"mix_status-{printed}-DELETE": "on"})
    assert editor.post(url, data)["Location"] == url
    assert not project.department_statuses.filter(name="Printed").exists()


def test_undo_reverts_a_department_rename_on_the_list_and_cues(
    editor: Client, project: models.Project
) -> None:
    cue = factories.CueFactory.create(project=project, mix_status="To Mix")
    url = _url("settings_departments", project) + "?list=mix_status"
    mix = list(project.department_statuses.filter(field="mix_status"))
    first = mix.index(next(o for o in mix if o.name == "To Mix"))
    data = _department_data(project, {f"mix_status-{first}-name": "Waiting"})
    editor.post(url, data)
    # One change set: Undo puts back the cue's label and the list's.
    history.undo(models.ChangeSet.objects.get(project=project))
    cue.refresh_from_db()
    assert cue.mix_status == "To Mix"
    names = project.department_statuses.filter(field="mix_status")
    assert "To Mix" in names.values_list("name", flat=True)
    assert not names.filter(name="Waiting").exists()


def test_saving_without_changes_records_nothing(
    editor: Client, project: models.Project
) -> None:
    url = _url("settings_departments", project) + "?list=mix_status"
    editor.post(url, _department_data(project))
    assert not models.ChangeSet.objects.filter(project=project).exists()


def test_grid_and_cue_page_use_the_projects_list(
    editor: Client, project: models.Project
) -> None:
    project.department_statuses.filter(field="licensing_status").delete()
    models.DepartmentStatus.objects.create(
        project=project,
        field="licensing_status",
        name="Cleared",
        color="#00aa00",
    )
    cue = factories.CueFactory.create(project=project, licensing_status="Old")
    data = editor.get(reverse("cues:api_grid", args=[project.pk])).json()
    column = next(c for c in data["columns"] if c["id"] == "licensing_status")
    assert [o["value"] for o in column["options"]] == ["", "Cleared"]
    edit = reverse("cues:api_cue", args=[cue.pk])
    good = editor.patch(
        edit,
        {
            "field": "licensing_status",
            "value": "Cleared",
            "updated_at": cue.updated_at.isoformat(),
        },
        content_type="application/json",
    )
    assert good.status_code == 200
    bad = editor.patch(
        edit,
        {
            "field": "licensing_status",
            "value": "Nope",
            "updated_at": models.Cue.objects.get(
                pk=cue.pk
            ).updated_at.isoformat(),
        },
        content_type="application/json",
    )
    assert "isn't in this project's list" in bad.content.decode()
    cue.licensing_status = "Old"  # a value since renamed elsewhere
    cue.save()
    html = editor.get(reverse("cues:cue", args=[cue.pk])).content.decode()
    assert '<option value="Old" selected>Old</option>' in html
    assert '<option value="Cleared">Cleared</option>' in html


# Statuses and lists ----------------------------------------------------------


def _status_lists(project: models.Project) -> dict[str, str]:
    """The statuses page's forms as they load, with the sync switch on."""
    statuses = list(project.statuses.all())
    subs = list(project.sub_statuses.all())
    data = {
        **_management("statuses", len(statuses), len(statuses)),
        **_management("subs", len(subs), len(subs)),
        "project-sync_review_status": "on",
    }
    for i, status in enumerate(statuses):
        data |= {
            f"statuses-{i}-id": str(status.pk),
            f"statuses-{i}-project": str(project.pk),
            f"statuses-{i}-label": status.label,
            f"statuses-{i}-kind": status.kind,
            f"statuses-{i}-color": status.color,
            f"statuses-{i}-order": str(status.order),
        }
    for i, sub in enumerate(subs):
        data |= {
            f"subs-{i}-id": str(sub.pk),
            f"subs-{i}-project": str(project.pk),
            f"subs-{i}-label": sub.label,
            f"subs-{i}-color": sub.color,
            f"subs-{i}-order": str(sub.order),
        }
    return data


def test_statuses_page_saves_the_sync_switch(
    editor: Client, project: models.Project
) -> None:
    url = _url("settings_statuses", project)
    assert editor.post(url, _status_lists(project))["Location"] == url
    project.refresh_from_db()
    assert project.sync_review_status


def test_a_sub_status_in_use_cant_be_deleted(
    editor: Client, project: models.Project
) -> None:
    url = _url("settings_statuses", project)
    subs = list(project.sub_statuses.all())
    used = factories.CueVersionFactory.create(
        cue=factories.CueFactory.create(project=project), sub_status=subs[0]
    )
    data = _status_lists(project) | {
        "subs-0-DELETE": "on",
        "subs-1-DELETE": "on",
    }
    page = editor.post(url, data).content.decode()
    assert f"{subs[0].label} is used by 1 version" in page
    assert project.sub_statuses.count() == len(subs)
    used.sub_status = None
    used.save()
    assert editor.post(url, data)["Location"] == url
    assert project.sub_statuses.count() == len(subs) - 2


def test_cue_sheet_page_saves(editor: Client, project: models.Project) -> None:
    url = _url("settings_cue_sheet", project)
    data = {
        "production_company": "Northlight",
        "numbering": "any",
        **_management("defaults", 0, 0),
    }
    assert editor.post(url, data)["Location"] == url
    project.refresh_from_db()
    assert project.production_company == "Northlight"


def _latest(project: models.Project) -> models.ChangeSet:
    return models.ChangeSet.objects.filter(project=project).latest("pk")


def test_a_statuses_save_is_one_undoable_change_set(
    editor: Client, project: models.Project
) -> None:
    url = _url("settings_statuses", project)
    statuses = list(project.statuses.all())
    composed = statuses.index(
        next(s for s in statuses if s.label == "Composed")
    )
    editor.post(
        url, _status_lists(project) | {f"statuses-{composed}-label": "Written"}
    )
    change_set = _latest(project)
    assert change_set.label == (
        "Edit statuses: sync with review status; rename Composed to Written"
    )
    history.undo(change_set)
    project.refresh_from_db()
    assert not project.sync_review_status
    assert project.statuses.filter(label="Composed").exists()


def test_saving_unchanged_statuses_records_nothing(
    editor: Client, project: models.Project
) -> None:
    data = _status_lists(project)
    del data["project-sync_review_status"]
    url = _url("settings_statuses", project)
    assert editor.post(url, data)["Location"] == url
    assert not models.ChangeSet.objects.filter(project=project).exists()


def test_saving_unchanged_lists_or_cue_sheet_records_nothing(
    editor: Client, project: models.Project
) -> None:
    lists = {**_management("themes", 0, 0), **_management("instruments", 0, 0)}
    url = _url("settings_lists", project)
    assert editor.post(url, lists)["Location"] == url
    cue_sheet = {
        "cue_number_pattern": project.cue_number_pattern,
        "numbering": "standard",
        **_management("defaults", 0, 0),
    }
    url = _url("settings_cue_sheet", project)
    assert editor.post(url, cue_sheet)["Location"] == url
    assert not models.ChangeSet.objects.filter(project=project).exists()


def test_a_lists_save_is_one_undoable_change_set(
    editor: Client, project: models.Project
) -> None:
    url = _url("settings_lists", project)
    data = {
        **_management("themes", 1, 0),
        **_management("instruments", 0, 0),
        "themes-0-name": "Love",
        "themes-0-color": "#aa3366",
        "themes-0-order": "0",
    }
    assert editor.post(url, data)["Location"] == url
    change_set = _latest(project)
    assert change_set.label == "Edit lists: add Love"
    history.undo(change_set)
    assert not project.themes.exists()


def test_a_lists_label_names_changes_and_deletions(
    editor: Client, project: models.Project
) -> None:
    strings = models.InstrumentOption.objects.create(
        project=project, name="Strings", color="#111111"
    )
    brass = models.InstrumentOption.objects.create(
        project=project, name="Brass", color="#222222", order=1
    )
    data = {
        **_management("themes", 0, 0),
        **_management("instruments", 3, 2),
        "instruments-0-id": str(brass.pk),
        "instruments-0-name": "Brass",
        "instruments-0-color": "#222222",
        "instruments-0-order": "1",
        "instruments-0-DELETE": "on",
        "instruments-1-id": str(strings.pk),
        "instruments-1-name": "Strings",
        "instruments-1-color": "#333333",
        "instruments-1-order": "0",
        # A row added and deleted again before saving: nothing to say.
        "instruments-2-name": "Harp",
        "instruments-2-color": "#444444",
        "instruments-2-order": "2",
        "instruments-2-DELETE": "on",
    }
    url = _url("settings_lists", project)
    assert editor.post(url, data)["Location"] == url
    assert _latest(project).label == "Edit lists: change Strings; delete Brass"


def test_a_cue_sheet_save_with_default_shares_undoes(
    editor: Client, project: models.Project
) -> None:
    party = models.Party.objects.create(
        kind="writer", first_name="Jane", last_name="Doe"
    )
    url = _url("settings_cue_sheet", project)
    data = {
        "production_company": "Northlight",
        "numbering": "standard",
        **_management("defaults", 1, 0),
        "defaults-0-party": str(party.pk),
        "defaults-0-role": "C",
        "defaults-0-share": "100",
        "defaults-0-order": "0",
    }
    assert editor.post(url, data)["Location"] == url
    change_set = _latest(project)
    assert change_set.label == (
        "Edit cue sheet settings: details; add Jane Doe C 100%"
    )
    history.undo(change_set)
    project.refresh_from_db()
    assert project.production_company == ""
    assert not project.default_shares.exists()


def test_a_series_default_share_undoes_with_the_cue_sheet(
    editor: Client, project: models.Project
) -> None:
    project.series = models.Series.objects.create(title="Harbour")
    project.kind = models.Project.Kind.EPISODE
    project.save()
    party = models.Party.objects.create(
        kind="writer", first_name="Casey", last_name="Composer"
    )
    data = {
        "numbering": "standard",
        **_management("defaults", 0, 0),
        **_management("series", 1, 0),
        "series-0-party": str(party.pk),
        "series-0-role": "C",
        "series-0-share": "100",
        "series-0-order": "0",
    }
    editor.post(_url("settings_cue_sheet", project), data)
    history.undo(_latest(project))
    assert not project.series.default_shares.exists()


# Views ----------------------------------------------------------------------


def _own_view(project: models.Project, name: str) -> models.View:
    return view_presets.create(project, name, [{"id": "number"}])


def test_views_page_actions(editor: Client, project: models.Project) -> None:
    first = _own_view(project, "Mine")
    second = _own_view(project, "Theirs")
    url = _url("settings_views", project)
    listing = editor.get(url).content.decode()
    assert "Mine" in listing and "Standard" in listing
    action = _url("settings_view_action", project)

    def post(view: models.View, **data: str) -> str:
        response = editor.post(action, {"view": str(view.pk), **data})
        assert response["Location"] == url
        return unescape(editor.get(url).content.decode())

    assert "Renamed Mine." in post(first, action="rename", name="Spotting 2")
    first.refresh_from_db()
    assert first.name == "Spotting 2"
    post(second, action="up")
    own = [v.name for v in view_presets.for_project(project)][-2:]
    assert own == ["Theirs", "Spotting 2"]
    assert "Made Theirs 2." in post(second, action="duplicate")
    assert "There's already a view called" in post(
        first, action="rename", name="Theirs"
    )
    standard = view_presets.for_project(project)[0]
    assert "keeps its name" in post(standard, action="rename", name="X")
    changed = view_presets.update(project, standard, {"sort": []})
    assert "(changed)" in editor.get(url).content.decode()
    assert "Reset Standard." in post(changed, action="reset")
    assert "Deleted Spotting 2." in post(first, action="delete")
    assert editor.post(action, {"view": "0"}).status_code == 404
    response = editor.post(action, {"view": str(second.pk), "action": "x"})
    assert response.status_code == 404


def test_moving_views(project: models.Project) -> None:
    first = _own_view(project, "One")
    second = _own_view(project, "Two")
    _own_view(project, "Three")  # keeps its place when the others swap
    view_presets.move(project, first, -1)  # already first: nothing
    view_presets.move(project, first, 1)
    names = [v.name for v in view_presets.for_project(project)][-3:]
    assert names == ["Two", "One", "Three"]
    view_presets.move(project, second, -1)  # now first: nothing
    standard = view_presets.for_project(project)[0]
    with pytest.raises(view_presets.ViewError):
        view_presets.move(project, standard, 1)


def test_copy_views_and_lists_from_another_project(
    editor: Client, project: models.Project
) -> None:
    source = factories.ProjectFactory.create(title="Pilot")
    _own_view(source, "Mine")
    _own_view(project, "Mine")
    standard = view_presets.for_project(source)[0]
    view_presets.update(
        source, standard, {"sort": [{"id": "title", "dir": "asc"}]}
    )
    view_presets.update(
        project,
        view_presets.for_project(project)[0],
        {"sort": [{"id": "number", "dir": "desc"}]},
    )
    models.ThemeOption.objects.create(
        project=source, name="Sea", color="#336699"
    )
    source.department_statuses.create(
        field="mix_status", name="Stems Out", color="#123456"
    )
    models.SubStatusOption.objects.create(
        project=source, label="Waiting", color="#123456"
    )
    models.StatusOption.objects.create(
        project=source, label="Sketch", kind="in_progress", color="#123456"
    )
    url = _url("settings_copy_from", project)
    html = editor.post(url, {"source": str(source.pk)}).content.decode()
    assert "Choose what to copy." in html
    response = editor.post(
        url, {"source": str(source.pk), "views": "on", "lists": "on"}
    )
    assert response["Location"] == _url("settings_views", project)
    names = [v.name for v in view_presets.for_project(project)]
    assert "Mine 2" in names
    standard = view_presets.for_project(project)[0]
    assert standard.sort == [{"id": "title", "dir": "asc"}]
    assert project.themes.filter(name="Sea").exists()
    assert project.department_statuses.filter(name="Stems Out").exists()
    assert project.sub_statuses.filter(label="Waiting").exists()
    assert project.statuses.filter(label="Sketch").exists()
    # The lists came over as one change set, so Undo takes them away.
    assert _latest(project).label == "Copy lists from Pilot"
    # Again: nothing new to add, and no empty change set.
    editor.post(url, {"source": str(source.pk), "lists": "on"})
    assert models.ChangeSet.objects.filter(project=project).count() == 1
    history.undo(_latest(project))
    assert not project.statuses.filter(label="Sketch").exists()
    assert not project.themes.filter(name="Sea").exists()
    project_admin.copy_lists(project, source)
    assert project_admin.copy_lists(project, source) == 0
    page = editor.get(_url("settings_views", project)).content.decode()
    assert "Pilot" in page


def test_no_other_projects_to_copy_from(editor: Client) -> None:
    alone = factories.ProjectFactory.create()
    models.Project.objects.exclude(pk=alone.pk).delete()
    html = editor.get(_url("settings_views", alone)).content.decode()
    assert "There are no other projects yet." in html


def test_one_view_one_entry_copied(
    editor: Client, project: models.Project
) -> None:
    source = factories.ProjectFactory.create(title="Pilot")
    _own_view(source, "Mine")
    source.statuses.all().delete()
    source.sub_statuses.all().delete()
    source.department_statuses.all().delete()
    models.ThemeOption.objects.create(
        project=source, name="Sea", color="#336699"
    )
    response = editor.post(
        _url("settings_copy_from", project),
        {"source": str(source.pk), "views": "on", "lists": "on"},
        follow=True,
    )
    assert "Copied 1 view and 1 list entry from" in response.content.decode()


# Project ----------------------------------------------------------------------


def test_duplicate(editor: Client, project: models.Project) -> None:
    factories.DeadlineFactory.create(project=project)
    _own_view(project, "Mine")
    models.ThemeOption.objects.create(
        project=project, name="Sea", color="#336699"
    )
    project.department_statuses.filter(field="mix_status").delete()
    factories.CueFactory.create(project=project)
    page = editor.get(_url("settings_project", project)).content.decode()
    assert 'value="The Voyage (copy)"' in page
    response = editor.post(
        _url("project_duplicate", project), {"title": "The Voyage II"}
    )
    copy = models.Project.objects.get(title="The Voyage II")
    assert response["Location"] == _url("settings", copy)
    assert copy.frame_rate == project.frame_rate
    assert not copy.cues.exists()
    assert copy.deadlines.count() == 1
    assert copy.themes.filter(name="Sea").exists()
    assert not copy.department_statuses.filter(field="mix_status").exists()
    assert "Mine" in [v.name for v in view_presets.for_project(copy)]
    assert copy.statuses.count() == project.statuses.count()


def test_duplicate_refuses_a_bad_title(
    editor: Client, project: models.Project
) -> None:
    html = editor.post(_url("project_duplicate", project), {"title": ""})
    assert "This field is required." in html.content.decode()
    too_long = editor.post(
        _url("project_duplicate", project), {"title": "x" * 201}
    )
    assert too_long.status_code == 200


def test_duplicate_reports_model_errors(
    editor: Client, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ValidationError("Not like that.")

    monkeypatch.setattr(project_admin, "duplicate", refuse)
    html = editor.post(_url("project_duplicate", project), {"title": "X"})
    assert "Not like that." in html.content.decode()


def test_archive_and_restore(editor: Client, project: models.Project) -> None:
    url = _url("project_archive", project)
    editor.post(url, {"archived": "1"})
    project.refresh_from_db()
    assert project.archived
    page = editor.get(_url("settings_project", project)).content.decode()
    assert "Unarchive Project" in page
    editor.post(url, {"archived": "0"})
    project.refresh_from_db()
    assert not project.archived


def test_delete_is_for_admins_with_the_title(
    editor: Client,
    project: models.Project,
    role_client: Callable[[Role], Client],
    settings: Any,
    tmp_path: pathlib.Path,
) -> None:
    settings.DATA_DIR = tmp_path
    cue = factories.CueFactory.create(project=project)
    version = factories.CueVersionFactory.create(cue=cue)
    cue.current_version = version
    cue.thumbnail.save("still.webp", ContentFile(b"x"))
    stored = cue.thumbnail.name
    assert stored is not None
    assert thumbnails.storage().exists(stored)
    url = _url("project_delete", project)
    # An editor doesn't see Delete at all.
    assert (
        "Delete Project"
        not in editor.get(_url("settings_project", project)).content.decode()
    )
    assert editor.post(url, {"confirm": "The Voyage"}).status_code == 403
    admin = role_client(Role.ADMIN)
    page = unescape(admin.post(url, {"confirm": "the voyage"}).content.decode())
    assert "That isn't the project's title." in page
    response = admin.post(url, {"confirm": "The Voyage"})
    assert response["Location"] == reverse("cues:dashboard")
    assert not models.Project.objects.filter(pk=project.pk).exists()
    assert not thumbnails.storage().exists(stored)


# App settings -----------------------------------------------------------------


def test_app_settings(
    editor: Client, role_client: Callable[[Role], Client]
) -> None:
    url = reverse("cues:app_settings")
    html = editor.get(url).content.decode()
    assert "Leading Zero (01:00:10:00)" in html
    assert f'href="{url}"' in html  # the top bar's link
    assert '<fieldset class="settings__fields">' in html  # editable
    assert "Most Work a Day (Min)" in html
    assert 'placeholder="As Deadlines Need"' in html
    data = {
        "theme": "light",
        "date_format": "Y-m-d",
        "leading_zero": "on",
        "single_click_edit": "on",
        "enter_behaviour": "save",
        "version_increment": "minor",
        "music_totals": "all",
        "schedule_easy": "1",
        "schedule_moderate": "1.5",
        "schedule_hard": "4",
        "schedule_revise": "0.25",
        "schedule_unrated": "Moderate",
        "schedule_projects": "one",
        "daily_max": "12.5",
        "min_part": "5",
    }
    assert editor.post(url, data)["Location"] == url
    saved = models.AppSettings.load()
    assert (saved.theme, saved.leading_zero, saved.subframes) == (
        "light",
        True,
        False,
    )
    # The schedule's minutes are kept as weighted seconds.
    assert saved.schedule_hard == Decimal(4)
    assert saved.schedule_unrated == "Moderate"
    assert saved.schedule_projects == models.AppSettings.ProjectsAtOnce.ONE
    assert (saved.schedule_daily_max, saved.schedule_min_part) == (750, 300)
    html = editor.get(url).content.decode()
    assert 'name="daily_max" value="12.5"' in html
    assert 'name="min_part" value="5"' in html
    assert editor.post(url, data | {"daily_max": ""})["Location"] == url
    assert models.AppSettings.load().schedule_daily_max is None
    refused = editor.post(url, data | {"schedule_hard": "0"})
    assert refused.status_code == 200
    assert editor.post(url, {"theme": "nope"}).status_code == 200
    viewer = role_client(Role.VIEWER)
    assert (
        f'href="{url}"'
        not in viewer.get(reverse("cues:dashboard")).content.decode()
    )
    assert viewer.post(url, data).status_code == 403
    # A viewer reads the settings: every control disabled, Save hidden.
    seen = viewer.get(url).content.decode()
    assert '<fieldset class="settings__fields" disabled>' in seen
    assert "View only." in seen


def test_more_edges(editor: Client, project: models.Project) -> None:
    # Ticking Remove on a row that was never saved does nothing.
    url = _url("settings_departments", project) + "?list=mix_status"
    count = project.department_statuses.filter(field="mix_status").count()
    data = _department_data(
        project,
        {
            f"mix_status-{count}-name": "Scratch",
            f"mix_status-{count}-color": "#336699",
            f"mix_status-{count}-order": "1",
            f"mix_status-{count}-DELETE": "on",
        },
        add_to="mix_status",
    )
    assert editor.post(url, data)["Location"] == url
    assert not project.department_statuses.filter(name="Scratch").exists()
    # A third copy is numbered 3.
    view = _own_view(project, "Mine")
    view_presets.duplicate(project, view)
    assert view_presets.duplicate(project, view).name == "Mine 3"
    # Copying views alone, then lists alone.
    source = factories.ProjectFactory.create(title="Pilot")
    _own_view(source, "Theirs")
    copy = _url("settings_copy_from", project)
    page = editor.post(
        copy, {"source": str(source.pk), "views": "on"}, follow=True
    ).content.decode()
    assert "Copied 1 view from" in page
    page = editor.post(
        copy, {"source": str(source.pk), "lists": "on"}, follow=True
    ).content.decode()
    assert "Copied 0 list entries from" in page


def test_viewers_see_settings_read_only(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    viewer = role_client(Role.VIEWER)
    page = viewer.get(_url("settings", project)).content.decode()
    assert "View only. Changing settings needs the editor role." in page
    assert 'class="notice"' not in page  # not the floating 404 card
    assert '<fieldset class="settings__fields" disabled>' in page


def test_editors_get_editable_settings(
    editor: Client, project: models.Project
) -> None:
    page = editor.get(_url("settings", project)).content.decode()
    assert "needs the editor role" not in page
    assert '<fieldset class="settings__fields">' in page


@pytest.mark.parametrize(
    ("choice", "day", "short"),
    [
        ("m/d/Y", "Thu Sep 17", "Sep 17"),
        ("d/m/Y", "Thu 17 Sep", "17 Sep"),
        ("Y-m-d", "Thu 17 Sep", "17 Sep"),
    ],
)
def test_dates_follow_the_date_format(
    editor: Client, choice: str, day: str, short: str
) -> None:
    settings = models.AppSettings.load()
    settings.date_format = choice
    settings.save()
    project = factories.ProjectFactory.create()
    turnover = factories.TurnoverFactory.create(
        project=project, received_date=datetime.date(2026, 9, 17)
    )
    picture = factories.PictureFactory.create(project=project)
    factories.PictureVersionFactory.create(picture=picture, turnover=turnover)
    page = editor.get(
        reverse("cues:picture", args=[project.pk])
    ).content.decode()
    assert day in page
    cue = factories.CueFactory.create(project=project, picture=picture)
    version = factories.CueVersionFactory.create(cue=cue)
    models.CueVersion.objects.filter(pk=version.pk).update(
        created_at=datetime.datetime(2026, 9, 17, 12, tzinfo=datetime.UTC)
    )
    page = editor.get(reverse("cues:cue", args=[cue.pk])).content.decode()
    assert short in page


def test_deadlines_page_keeps_project_and_shared_days_apart(
    editor: Client, project: models.Project
) -> None:
    other = factories.ProjectFactory.create()
    models.DayOff.objects.create(project=other, date=datetime.date(2099, 1, 2))
    url = _url("settings_deadlines", project)
    data = {
        **_management("deadlines", 0, 0),
        **_management("project-days", 2, 0),
        "project-days-0-date": "2099-01-02",
        "project-days-1-date": "2099-01-02",  # twice: kept once
    }
    assert editor.post(url, data)["Location"] == url
    mine = reverse("cues:days_off")
    shared = {**_management("days", 1, 0), "days-0-date": "2099-01-01"}
    assert editor.post(mine, shared)["Location"] == mine
    assert list(
        models.DayOff.objects.order_by("date", "project").values_list(
            "date", "project"
        )
    ) == [
        (datetime.date(2099, 1, 1), None),
        (datetime.date(2099, 1, 2), project.pk),
        (datetime.date(2099, 1, 2), other.pk),
    ]
    page = editor.get(url).content.decode()
    assert "This Project's Days Off" in page
    assert f'href="{mine}">Your Own Days Off</a>' in page


def test_viewers_see_their_days_off_read_only(
    role_client: Callable[[Role], Client],
) -> None:
    page = (
        role_client(Role.VIEWER).get(reverse("cues:days_off")).content.decode()
    )
    assert '<fieldset class="settings__fields" disabled>' in page
    assert ">Save</button>" not in page
