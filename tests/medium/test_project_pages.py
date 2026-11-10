"""Dashboard, project, settings and picture pages."""

from collections.abc import Callable
import datetime
import re
from typing import Any
from unittest import mock

from django.db import DatabaseError
from django.test import Client
from django.test import RequestFactory
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import defaults
from cues import models
from cues.services import demo
from cues.services import history
from cues.services import summaries
from reellife import context_processors
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def _management(prefix: str, total: int, initial: int = 0) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


# Dashboard


def test_dashboard_lists_projects_with_progress_and_map(editor: Client) -> None:
    project = demo.build_demo().project
    response = editor.get(reverse("cues:dashboard"))
    html = response.content.decode()
    assert project.title in html
    assert "Composed" in html
    assert 'class="cuemap"' in html
    assert "on older picture" in html
    assert "New Project" in html


def test_dashboard_search_and_archived(editor: Client) -> None:
    factories.ProjectFactory.create(
        title="The Lighthouse", production_number="LH1"
    )
    factories.ProjectFactory.create(title="Old Film", archived=True)
    found = editor.get("/projects/?q=lh1 light").content.decode()  # every word
    assert re.search(
        r'<li class="project tone-\d"[^>]*the lighthouse[^>]*"\s*\n', found
    )
    assert re.search(r"data-no-match hidden>", found)
    # A search only hides cards, so clearing it brings the rest back.
    missed = editor.get("/projects/?q=nothing").content.decode()
    assert re.search(
        r'<li class="project tone-\d"[^>]*the lighthouse[^>]*" hidden', missed
    )
    assert re.search(r"data-no-match>.*No Projects Match", missed, re.S)
    assert (
        "Old Film" not in editor.get(reverse("cues:dashboard")).content.decode()
    )
    shown = editor.get("/projects/?archived=on")
    assert "Old Film" in shown.content.decode()
    maps = {s.project.title: s.cue_map for s in shown.context["summaries"]}
    assert maps["Old Film"] is None  # archived: no cue map to draw
    assert maps["The Lighthouse"] is not None


def test_viewers_dont_see_new_project(
    role_client: Callable[[Role], Client],
) -> None:
    html = role_client(Role.VIEWER).get("/").content.decode()
    assert "New Project" not in html


# Creating a project


def new_project_data(**overrides: str) -> dict[str, str]:
    data = {
        "title": "The Big Heist",
        "production_number": "BH",
        "kind": "film",
        "frame_rate": "23.976",
        "picture_type": "reels",
        "lanes": "2",
        "numbering": "standard",
        "deadlines-composition_start": "2026-11-01",
        "deadlines-composition_end": "2026-12-04",
        "deadlines-mix_end": "2026-12-11",
        "deadlines-delivery": "2026-12-18",
        # The First Turnover fold, left empty.
        "reels-TOTAL_FORMS": "3",
        "reels-INITIAL_FORMS": "0",
        "reels-MIN_NUM_FORMS": "0",
        "reels-MAX_NUM_FORMS": "40",
    }
    data.update(overrides)
    return data


def test_create_project_with_deadlines(editor: Client) -> None:
    response = editor.post(reverse("cues:project_create"), new_project_data())
    project = models.Project.objects.get(title="The Big Heist")
    assert response["Location"] == reverse("cues:project", args=[project.pk])
    assert project.statuses.count() == 17
    assert not project.turnovers.exists()  # logged on the Picture page
    deadlines = {
        d.kind: (str(d.start_date), str(d.end_date))
        for d in project.deadlines.all()
    }
    assert deadlines == {
        "composition": ("2026-11-01", "2026-12-04"),
        "mix": ("None", "2026-12-11"),
        "delivery": ("None", "2026-12-18"),
    }
    assert project.cue_number_pattern == defaults.DEFAULT_CUE_NUMBER_PATTERN


def test_create_project_with_days_off(editor: Client) -> None:
    page = editor.get(reverse("cues:project_create")).content.decode()
    assert '<details class="days-off">' in page  # folded until wanted
    data = {
        **new_project_data(),
        **_management("days", 1),
        "days-0-date": "2026-12-25",
        "days-0-note": "Christmas",
    }
    editor.post(reverse("cues:project_create"), data)
    assert models.DayOff.objects.get().note == "Christmas"
    bad = {**data, "title": "Again", "days-0-date": "soon"}
    page = editor.post(reverse("cues:project_create"), bad).content.decode()
    assert '<details class="days-off" open>' in page
    assert not models.Project.objects.filter(title="Again").exists()


def test_a_day_already_off_doesnt_block_saving(editor: Client) -> None:
    models.DayOff.objects.create(date=datetime.date(2026, 12, 25), note="Xmas")
    data = {
        **new_project_data(),
        **_management("days", 3),
        "days-0-date": "2026-12-25",  # already off (another tab added it)
        "days-1-date": "2026-12-31",
        "days-2-date": "2026-12-31",  # twice on this form
    }
    response = editor.post(reverse("cues:project_create"), data)
    assert response.status_code == 302
    assert sorted(
        (str(d.date), d.note) for d in models.DayOff.objects.all()
    ) == [("2026-12-25", "Xmas"), ("2026-12-31", "")]


def test_removing_and_adding_the_same_day_in_one_save(editor: Client) -> None:
    day = models.DayOff.objects.create(date=datetime.date(2026, 12, 25))
    data = {
        **_management("days", 2, initial=1),
        "days-0-id": str(day.pk),
        "days-0-date": "2026-12-25",
        "days-0-DELETE": "on",
        "days-1-date": "2026-12-25",
        "days-1-note": "Again",
    }
    editor.post(reverse("cues:days_off"), data)
    assert [d.note for d in models.DayOff.objects.all()] == ["Again"]


@pytest.mark.parametrize(
    ("numbering", "pattern", "stored"),
    [
        ("standard", "", defaults.DEFAULT_CUE_NUMBER_PATTERN),
        ("any", "", defaults.ANY_CUE_NUMBER_PATTERN),
        ("custom", r"^\d+m\d+$", r"^\d+m\d+$"),
        ("", r"^X\d+$", r"^X\d+$"),  # no choice posted: keep the pattern
        ("", "", defaults.DEFAULT_CUE_NUMBER_PATTERN),
    ],
)
def test_numbering_choices(
    editor: Client, numbering: str, pattern: str, stored: str
) -> None:
    editor.post(
        reverse("cues:project_create"),
        new_project_data(numbering=numbering, cue_number_pattern=pattern),
    )
    assert models.Project.objects.get().cue_number_pattern == stored


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"drop_frame": "on"}, "Drop-frame is only used at 29.97 or 59.94."),
        (
            {"deadlines-composition_end": "2026-10-01"},
            "The end is before the start.",
        ),
        ({"deadlines-composition_end": ""}, "Add an end date."),
        ({"numbering": "custom"}, "Enter a pattern, or pick another choice."),
        (
            {"numbering": "custom", "cue_number_pattern": "("},
            "Not a valid pattern",
        ),
    ],
)
def test_create_project_errors(
    editor: Client, overrides: dict[str, str], error: str
) -> None:
    response = editor.post(
        reverse("cues:project_create"), new_project_data(**overrides)
    )
    assert response.status_code == 200
    assert error in response.content.decode()
    assert not models.Project.objects.exists()


def test_create_form_renders(editor: Client) -> None:
    html = editor.get(reverse("cues:project_create")).content.decode()
    assert "Deadlines" in html
    assert 'name="deadlines-orchestration_start"' in html
    assert "First turnover" not in html
    assert "data-dropzone" in html
    assert 'value="standard"' in html and "checked" in html
    # Automatic shows the first colour until the project exists.
    assert "swatches__choice tone-0 swatches__choice--auto" in html


def test_settings_show_the_projects_numbering(editor: Client) -> None:
    project = factories.ProjectFactory.create(
        cue_number_pattern=defaults.ANY_CUE_NUMBER_PATTERN
    )
    html = editor.get(
        reverse("cues:settings_cue_sheet", args=[project.pk])
    ).content.decode()
    assert 'value="any" id="id_numbering_1" checked' in html
    custom = factories.ProjectFactory.create(cue_number_pattern=r"^Q\d+$")
    html = editor.get(
        reverse("cues:settings_cue_sheet", args=[custom.pk])
    ).content.decode()
    assert "^Q\\d+$" in html


# Project page


def test_project_page_hosts_the_grid(editor: Client) -> None:
    project = demo.build_demo().project
    response = editor.get(reverse("cues:project", args=[project.pk]))
    html = response.content.decode()
    assert 'id="cue-grid"' in html
    assert "ag-grid-community.min.js" in html
    config = response.context["grid_config"]
    assert config["dataUrl"] == reverse("cues:api_grid", args=[project.pk])
    # The find field looks in the scene and notes as well.
    assert {"number", "title", "scene", "spotting_notes", "mix_notes"} <= set(
        config["searchFields"]
    )
    assert config["styleNonce"]
    # The nonce in the config is the one the CSP header allows.
    csp = response.headers["Content-Security-Policy"]
    assert f"'nonce-{config['styleNonce']}'" in csp
    assert 'id="grid-undo"' in html


def test_viewers_get_no_undo_button(
    role_client: Callable[[Role], Client],
) -> None:
    project = factories.ProjectFactory.create()
    html = (
        role_client(Role.VIEWER)
        .get(reverse("cues:project", args=[project.pk]))
        .content.decode()
    )
    assert 'id="grid-undo"' not in html


def test_empty_project_page(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    html = editor.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert "No cues yet" in html
    assert "Composed 0 / 0" not in html
    assert 'class="progress-bar progress-bar--empty"' in html


def test_missing_project_is_404(editor: Client) -> None:
    assert editor.get(reverse("cues:project", args=[999])).status_code == 404


# Settings


def test_general_settings_save(editor: Client) -> None:
    project = factories.ProjectFactory.create(title="Old")
    url = reverse("cues:settings", args=[project.pk])
    response = editor.post(url, {"title": "New", "kind": "film"})
    assert response["Location"] == url
    picture = reverse("cues:settings_picture", args=[project.pk])
    data = {"frame_rate": "25", "picture_type": "reels", "lanes": "3"}
    assert editor.post(picture, data)["Location"] == picture
    project.refresh_from_db()
    assert (project.title, project.frame_rate, project.lanes) == (
        "New",
        "25",
        3,
    )


def test_frame_rate_locks_once_timecodes_exist(editor: Client) -> None:
    cue = factories.CueFactory.create()
    project = cue.project
    url = reverse("cues:settings_picture", args=[project.pk])
    assert (
        "Locked: the project has timecodes" in editor.get(url).content.decode()
    )
    data = {"frame_rate": "25", "picture_type": "reels", "lanes": "2"}
    editor.post(url, data)
    project.refresh_from_db()
    assert project.frame_rate == "23.976"


def _status_data(project: models.Project, **changes: str) -> dict[str, str]:
    statuses = list(project.statuses.all())
    subs = list(project.sub_statuses.all())
    data = {
        **_management("statuses", len(statuses) + 1, len(statuses)),
        **_management("subs", len(subs) + 1, len(subs)),
        **_management("themes", 1, 0),
        **_management("instruments", 1, 0),
    }
    for i, status in enumerate(statuses):
        data.update(
            {
                f"statuses-{i}-id": str(status.pk),
                f"statuses-{i}-project": str(project.pk),
                f"statuses-{i}-label": status.label,
                f"statuses-{i}-kind": status.kind,
                f"statuses-{i}-color": status.color,
                f"statuses-{i}-order": str(status.order),
            }
        )
    for i, sub in enumerate(subs):
        data.update(
            {
                f"subs-{i}-id": str(sub.pk),
                f"subs-{i}-project": str(project.pk),
                f"subs-{i}-label": sub.label,
                f"subs-{i}-color": sub.color,
                f"subs-{i}-order": str(sub.order),
            }
        )
    # The blank "add" rows, as a browser posts them.
    data.update(
        {
            f"statuses-{len(statuses)}-color": "#6b7280",
            f"statuses-{len(statuses)}-order": "0",
            f"subs-{len(subs)}-color": "#6b7280",
            f"subs-{len(subs)}-order": "0",
            "themes-0-color": "#6b7280",
            "themes-0-order": "0",
            "instruments-0-color": "#6b7280",
            "instruments-0-order": "0",
        }
    )
    data.update(changes)
    return data


def test_untouched_add_rows_are_ignored(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:settings_statuses", args=[project.pk])
    assert editor.post(url, _status_data(project))["Location"] == url
    assert project.statuses.count() == 17


def test_statuses_and_sub_statuses_can_be_edited(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:settings_statuses", args=[project.pk])
    n = project.statuses.count()
    data = _status_data(
        project,
        **{
            "statuses-0-label": "To Spot",
            "statuses-0-color": "#123456",
            f"statuses-{n}-label": "Printed",
            f"statuses-{n}-kind": "approved",
            f"statuses-{n}-color": "#abcdef",
            f"statuses-{n}-order": "99",
            "subs-0-DELETE": "on",
            "statuses-14-DELETE": "on",  # Omit, unused here
        },
    )
    response = editor.post(url, data)
    assert response["Location"] == url
    labels = list(project.statuses.values_list("label", flat=True))
    assert labels[0] == "To Spot" and "Printed" in labels
    assert "Omit" not in labels
    assert project.sub_statuses.count() == 6


def test_status_in_use_cannot_be_removed(editor: Client) -> None:
    cue = factories.CueFactory.create()
    project = cue.project
    status = project.statuses.get(label="Omit")
    cue.status = status
    cue.save()
    index = list(project.statuses.all()).index(status)
    response = editor.post(
        reverse("cues:settings_statuses", args=[project.pk]),
        _status_data(project, **{f"statuses-{index}-DELETE": "on"}),
    )
    assert response.status_code == 200
    assert "Omit is used by 1 cue; give them another status first." in (
        response.content.decode()
    )
    assert project.statuses.filter(pk=status.pk).exists()


def test_deadlines_can_be_added(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:settings_deadlines", args=[project.pk])
    assert editor.get(url).status_code == 200
    data = {
        **_management("deadlines", 1),
        "deadlines-0-kind": "mix",
        "deadlines-0-end_date": "2026-12-18",
        **_management("project-days", 1),
        "project-days-0-date": "2026-12-25",
        "project-days-0-note": "Studio closed",
    }
    assert editor.post(url, data)["Location"] == url
    assert project.deadlines.get().kind == "mix"
    assert models.DayOff.objects.get(project=project).note == "Studio closed"
    html = editor.get(url).content.decode()
    assert 'value="Studio closed"' in html
    # No blank row (Add Row adds one), and blanks read "Choose…".
    assert 'name="deadlines-TOTAL_FORMS" value="1"' in html
    assert '<option value="">Choose…</option>' in html


def test_invalid_deadlines_rerender(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:settings_deadlines", args=[project.pk])
    data = {
        **_management("deadlines", 1),
        "deadlines-0-kind": "mix",
        "deadlines-0-start_date": "2026-12-19",
        "deadlines-0-end_date": "2026-12-18",
        **_management("days", 0),
    }
    assert editor.post(url, data).status_code == 200
    assert not project.deadlines.exists()


# Picture


def test_picture_page_shows_reels_by_turnover(editor: Client) -> None:
    project = demo.build_demo().project
    html = editor.get(
        reverse("cues:picture", args=[project.pk])
    ).content.decode()
    assert "c1030" in html and "c0917" in html
    assert "2:00:02:12" in html
    assert "reel-version--current" in html


def test_picture_page_without_turnovers(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    html = editor.get(
        reverse("cues:picture", args=[project.pk])
    ).content.decode()
    assert "No Turnovers Yet" in html


def _turnover_data(**overrides: str) -> dict[str, str]:
    data = {
        "label": "c1101",
        "received_date": "2026-11-01",
        "notes": "",
        **_management("reels", 3, 3),
        "reels-0-include": "",
        "reels-0-tc_in": "1:00:00:00",
        "reels-0-tc_out": "1:20:12:00",
        "reels-1-include": "on",
        "reels-1-tc_in": "2:00:04:00",
        "reels-1-tc_out": "2:21:16:00",
        "reels-2-include": "",
        "reels-2-tc_in": "3:00:00:00",
        "reels-2-tc_out": "3:21:40:00",
        **_management("new", 3),
        "new-0-name": "4",
        "new-0-tc_in": "4:00:00:00",
        "new-0-tc_out": "4:10:00:00",
    }
    data.update(overrides)
    return data


def test_turnover_form_prefills_current_picture(editor: Client) -> None:
    project = demo.build_demo().project
    response = editor.get(reverse("cues:turnover_create", args=[project.pk]))
    html = response.content.decode()
    assert 'value="2:00:02:12"' in html  # reel 2 from c1030
    assert 'value="1:00:00:00"' in html  # reel 1 still from c0917


def test_logging_a_turnover(editor: Client) -> None:
    project = demo.build_demo().project
    url = reverse("cues:turnover_create", args=[project.pk])
    response = editor.post(url, _turnover_data(), follow=True)
    assert "Logged c1101." in response.content.decode()
    assert "now on older picture" in response.content.decode()
    turnover = project.turnovers.get(label="c1101")
    reels = sorted(
        turnover.picture_versions.values_list("picture__name", flat=True)
    )
    assert reels == ["2", "4"]
    assert project.pictures.get(name="4").order == 4


def test_turnover_errors_rerender(editor: Client) -> None:
    project = demo.build_demo().project
    url = reverse("cues:turnover_create", args=[project.pk])
    bad_tc = editor.post(url, _turnover_data(**{"reels-1-tc_in": "nope"}))
    assert "not a timecode" in bad_tc.content.decode()
    duplicate = editor.post(url, _turnover_data(label="c0917"))
    assert duplicate.status_code == 200
    assert not project.turnovers.filter(received_date="2026-11-01").exists()


def test_turnover_refuses_when_reels_changed_meanwhile(editor: Client) -> None:
    project = demo.build_demo().project
    url = reverse("cues:turnover_create", args=[project.pk])
    data = _turnover_data(**_management("reels", 2, 2))
    response = editor.post(url, data)
    assert (
        "The reels changed while you were editing" in response.content.decode()
    )


def test_turnover_form_with_a_reel_missing_picture(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    factories.PictureFactory.create(project=project, name="1")
    url = reverse("cues:turnover_create", args=[project.pk])
    html = editor.get(url).content.decode()
    assert 'name="reels-0-tc_in"' in html
    assert 'value="1:' not in html


def test_reel_forms_need_a_rule_for_required_times() -> None:
    from cues import forms  # noqa: PLC0415 pylint: disable=import-outside-toplevel

    with pytest.raises(NotImplementedError):
        forms._ReelTimesForm().wants_times({})  # noqa: SLF001 pylint: disable=protected-access


def test_turnover_form_for_a_project_without_reels(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:turnover_create", args=[project.pk])
    assert "Reels in this turnover" not in editor.get(url).content.decode()
    data = {
        "label": "c0001",
        "received_date": "2026-01-01",
        **_management("reels", 0),
        **_management("new", 1),
        "new-0-name": "1",
        "new-0-tc_in": "1:00:00:00",
        "new-0-tc_out": "1:10:00:00",
    }
    response = editor.post(url, data, follow=True)
    assert "Logged c0001." in response.content.decode()
    assert "older picture" not in response.content.decode()


@pytest.mark.parametrize(
    ("row", "error"),
    [
        ({"new-0-tc_in": "1:00:00:24"}, "frames must be &lt; 24"),
        ({"new-0-tc_out": "0:59:00:00"}, "OUT must be after IN."),
        ({"new-0-tc_out": ""}, "Give its IN and OUT."),
    ],
)
def test_new_reel_rows_are_checked(
    editor: Client, row: dict[str, str], error: str
) -> None:
    project = factories.ProjectFactory.create(frame_rate="24")
    data = {
        "label": "c0001",
        "received_date": "2026-01-01",
        **_management("reels", 0),
        **_management("new", 1),
        "new-0-name": "1",
        "new-0-tc_in": "1:00:00:00",
        "new-0-tc_out": "1:10:00:00",
        **row,
    }
    response = editor.post(
        reverse("cues:turnover_create", args=[project.pk]), data
    )
    assert error in response.content.decode()
    assert not project.turnovers.exists()


# Services behind the pages


def test_next_deadline() -> None:
    project = factories.ProjectFactory.create()
    today = datetime.date(2026, 11, 2)
    assert summaries.next_deadline(project, today) is None
    factories.DeadlineFactory.create(
        project=project, end_date=datetime.date(2026, 11, 1)
    )
    factories.DeadlineFactory.create(
        project=project, kind="mix", end_date=datetime.date(2026, 11, 10)
    )
    deadline = summaries.next_deadline(project, today)
    assert deadline is not None
    assert (deadline.label, deadline.days_left, deadline.soon) == (
        "Mix",
        8,
        True,
    )


def test_theme_falls_back_when_the_database_fails() -> None:
    request = RequestFactory().get("/")
    with mock.patch.object(
        models.AppSettings, "load", side_effect=DatabaseError("down")
    ):
        assert context_processors.preferences(request) == {
            "theme": "dark",
            "date_format": "m/d/Y",
            "short_format": "M j",
            "day_format": "D M j",
        }


def test_theme_from_settings(editor: Client) -> None:
    settings = models.AppSettings.load()
    settings.theme = "light"
    settings.save()
    assert (
        'data-theme="light"'
        in editor.get(reverse("cues:dashboard")).content.decode()
    )


def test_tc_filter_handles_unset_values() -> None:
    from cues.templatetags import timecodes  # noqa: PLC0415 pylint: disable=import-outside-toplevel

    project: Any = factories.ProjectFactory.build()
    assert timecodes.tc(None, project) == ""


def test_project_bar_and_progress_segments(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    picture = factories.PictureFactory.create(project=project)
    statuses = {s.label: s for s in project.statuses.all()}
    for number, status, frames in (
        ("1m01", "Approved", 300),
        ("1m02", "To Write", 100),
    ):
        factories.CueFactory.create(
            project=project,
            picture=picture,
            number=number,
            status=statuses[status],
            tc_in=0,
            tc_out=frames,
        )
    html = editor.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    text = " ".join(html.split())
    assert '<span class="project-bar__label">Composed</span>' in text
    assert "1 / 2 cues (75%)" in text
    assert (
        'class="progress-bar__approved" x="0.0000" y="0" width="75.0000"'
        in text
    )
    assert "<title>To Write (25%)</title>" in text
    assert 'aria-label="Approved 75%, To Write 25%"' in text
    # The breakdown fold, and its line on the Projects card.
    assert '<details class="breakdown" data-remember="breakdown">' in text
    assert "<dt>Total</dt>" in text
    # The overview's own links: Cue details and New cue.
    assert 'id="grid-details"' in html
    assert 'id="grid-new-cue"' in html
    for size in ("Tiny", "Small", "Medium", "Large", "Auto"):
        assert f"<span>{size}</span>" in html


def test_other_project_pages_share_the_bar(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    html = editor.get(
        reverse("cues:picture", args=[project.pk])
    ).content.decode()
    assert 'class="project-bar"' in html
    assert f'href="/projects/{project.pk}/picture/" aria-current="page"' in html
    assert 'id="grid-new-cue"' not in html


def test_themes_and_instruments_are_project_lists(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:settings_lists", args=[project.pk])
    data = {
        **_management("themes", 1, 0),
        **_management("instruments", 1, 0),
        "themes-0-name": "Aurora",
        "themes-0-color": "#10b981",
        "themes-0-order": "0",
        "instruments-0-name": "Strings",
        "instruments-0-color": "#10b981",
        "instruments-0-order": "0",
    }
    assert editor.post(url, data)["Location"] == url
    assert str(project.themes.get()) == "Aurora"
    assert str(project.instruments.get()) == "Strings"
    html = editor.get(url).content.decode()
    assert "<h3>Themes</h3>" in html and "<h3>Instruments</h3>" in html


# Bulk actions on the Projects page


def _bulk(
    client: Client, action: str, *projects: models.Project, **extra: str
) -> Any:
    return client.post(
        reverse("cues:projects_bulk"),
        {"action": action, "project": [str(p.pk) for p in projects], **extra},
        follow=True,
    )


def test_bulk_archive_unarchive_and_duplicate(editor: Client) -> None:
    one = factories.ProjectFactory.create(title="One")
    two = factories.ProjectFactory.create(title="Two")
    page = _bulk(editor, "archive", one, two).content.decode()
    assert "Archived 2 projects." in page
    assert models.Project.objects.filter(archived=True).count() == 2
    page = _bulk(editor, "unarchive", one).content.decode()
    assert "Unarchived 1 project." in page
    one.refresh_from_db()
    assert not one.archived
    page = _bulk(editor, "duplicate", one).content.decode()
    assert "Duplicated 1 project, without their cues." in page
    assert models.Project.objects.filter(title="One (copy)").exists()


def test_bulk_needs_projects_and_a_known_action(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    assert (
        "Tick the projects first." in _bulk(editor, "archive").content.decode()
    )
    page = _bulk(editor, "delete", project).content.decode()
    assert "isn&#x27;t something the projects can do" in page
    assert models.Project.objects.filter(pk=project.pk).exists()


def test_bulk_goes_back_to_the_same_list_but_never_elsewhere(
    editor: Client,
) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:projects_bulk")
    data = {"action": "archive", "project": [str(project.pk)]}
    back = editor.post(url, {**data, "next": "/?archived=on"})
    assert back["Location"] == "/?archived=on"
    away = editor.post(url, {**data, "next": "https://evil.example/"})
    assert away["Location"] == reverse("cues:dashboard")


def test_bulk_is_for_editors(
    role_client: Callable[[Role], Client], editor: Client
) -> None:
    project = factories.ProjectFactory.create()
    viewer = role_client(Role.VIEWER)
    assert _bulk(viewer, "archive", project).status_code == 403
    assert (
        "data-bulk-item"
        not in viewer.get(reverse("cues:dashboard")).content.decode()
    )
    page = editor.get(reverse("cues:dashboard")).content.decode()
    assert f'value="{project.pk}" form="bulk-form"' in page


@pytest.mark.parametrize(
    ("sort", "order"),
    [
        ("title", ["Alpha", "Bravo", "Charlie"]),
        ("number", ["Charlie", "Alpha", "Bravo"]),
        ("changed", ["Bravo", "Charlie", "Alpha"]),
        ("deadline", ["Bravo", "Alpha", "Charlie"]),
        ("nonsense", ["Bravo", "Charlie", "Alpha"]),
    ],
)
def test_projects_sort(editor: Client, sort: str, order: list[str]) -> None:
    alpha = factories.ProjectFactory.create(
        title="Alpha", production_number="B2"
    )
    charlie = factories.ProjectFactory.create(
        title="Charlie", production_number="A1"
    )
    bravo = factories.ProjectFactory.create(title="Bravo", production_number="")
    alpha.deadlines.create(
        kind="composition", end_date=datetime.date(2099, 2, 1)
    )
    bravo.deadlines.create(
        kind="composition", end_date=datetime.date(2099, 1, 1)
    )
    charlie.save()
    bravo.save()  # changed last
    page = editor.get(
        reverse("cues:dashboard"), {"sort": sort}
    ).content.decode()
    positions = [page.index(f">{title}</a>") for title in order]
    assert positions == sorted(positions)


def test_projects_sort_direction_and_archived(editor: Client) -> None:
    factories.ProjectFactory.create(title="Alpha")
    factories.ProjectFactory.create(title="Bravo", archived=True)
    factories.ProjectFactory.create(title="Charlie")
    url = reverse("cues:dashboard")

    def order(**params: str) -> list[str]:
        page = editor.get(url, {"archived": "on", **params}).content.decode()
        titles = ["Alpha", "Bravo", "Charlie"]
        return sorted(titles, key=lambda t: page.index(f">{t}</a>"))

    # Archived projects sort among the rest.
    assert order(sort="title") == ["Alpha", "Bravo", "Charlie"]
    assert order(sort="title", dir="desc") == ["Charlie", "Bravo", "Alpha"]
    # The arrow submits the flipped direction after the current one.
    page = editor.get(f"{url}?archived=on&sort=title&dir=asc&dir=desc")
    assert page.content.decode().index(
        ">Charlie</a>"
    ) < page.content.decode().index(">Alpha</a>")
    assert order(sort="title", dir="sideways") == ["Alpha", "Bravo", "Charlie"]


# Editing and deleting turnovers


def _fresh_turnover(project: models.Project) -> models.Turnover:
    """c1101: reel 2 moved two seconds, and a new reel 4; nothing written."""
    from cues.services import turnovers  # noqa: PLC0415 pylint: disable=import-outside-toplevel

    return turnovers.create_turnover(
        project,
        label="c1101",
        received_date=datetime.date(2026, 11, 1),
        reels=[
            turnovers.ReelEntry(
                project.pictures.get(name="2"), "2", 3600 * 24 * 2, None
            ),
            turnovers.ReelEntry(None, "4", 3600 * 24 * 4, None),
        ],
    )


def _edit_data(**overrides: str) -> dict[str, str]:
    data = {
        "label": "c1102",
        "received_date": "2026-11-02",
        "notes": "Fixed the label",
        **_management("reels", 4, 4),
        "reels-0-include": "",
        "reels-1-include": "",  # drop reel 2 from it
        "reels-2-include": "",
        "reels-3-include": "on",
        "reels-3-tc_in": "4:00:00:00",
        "reels-3-tc_out": "4:12:00:00",
        **_management("new", 0),
    }
    data.update(overrides)
    return data


def test_editing_a_turnover(editor: Client) -> None:
    project = demo.build_demo().project
    turnover = _fresh_turnover(project)
    url = reverse("cues:turnover_edit", args=[project.pk, turnover.pk])
    html = editor.get(url).content.decode()
    assert "Edit Turnover c1101" in html and "In Turnover" in html
    assert 'value="4:00:00:00"' in html
    page = editor.post(url, _edit_data(), follow=True).content.decode()
    assert "Saved c1102." in page
    turnover.refresh_from_db()
    assert (turnover.label, turnover.notes) == ("c1102", "Fixed the label")
    versions = {
        v.picture.name: v.tc_out for v in turnover.picture_versions.all()
    }
    assert versions == {"4": 3600 * 24 * 4 + 12 * 60 * 24}
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    turnover.refresh_from_db()
    assert turnover.label == "c1101"
    assert turnover.picture_versions.count() == 2


def test_a_turnover_reel_with_cues_on_it_stays(editor: Client) -> None:
    project = demo.build_demo().project
    used = project.turnovers.get(label="c1030")
    url = reverse("cues:turnover_edit", args=[project.pk, used.pk])
    data = _edit_data(label="c1030", **_management("reels", 3, 3))
    page = editor.post(url, data).content.decode()
    assert "Can&#x27;t drop Reel" in page and "written to Reel" in page
    assert used.picture_versions.count() == 2
    stale = editor.post(url, _edit_data(**_management("reels", 9, 9)))
    assert "The reels changed while you were editing" in stale.content.decode()


def test_deleting_a_turnover(editor: Client) -> None:
    project = demo.build_demo().project
    used = project.turnovers.get(label="c1030")
    refused = editor.post(
        reverse("cues:turnover_delete", args=[project.pk, used.pk]),
        follow=True,
    ).content.decode()
    assert "Can&#x27;t delete c1030" in refused
    fresh = _fresh_turnover(project)
    done = editor.post(
        reverse("cues:turnover_delete", args=[project.pk, fresh.pk]),
        follow=True,
    ).content.decode()
    assert "Deleted c1101. Undo brings it back." in done
    assert not project.turnovers.filter(label="c1101").exists()
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    assert project.turnovers.get(label="c1101").picture_versions.count() == 2


def test_turnover_edit_links_are_for_editors(
    editor: Client, role_client: Callable[[Role], Client]
) -> None:
    project = demo.build_demo().project
    url = reverse("cues:picture", args=[project.pk])
    assert "Edit c1030" in editor.get(url).content.decode()
    viewer = role_client(Role.VIEWER)
    assert "Edit c1030" not in viewer.get(url).content.decode()
    turnover = project.turnovers.get(label="c1030")
    edit = reverse("cues:turnover_edit", args=[project.pk, turnover.pk])
    assert viewer.post(edit, _edit_data()).status_code == 403


def test_editing_adds_a_reel_and_returns_to_picture(editor: Client) -> None:
    project = factories.ProjectFactory.create(frame_rate="24")
    turnover = _fresh_turnover_plain(project)
    url = reverse("cues:turnover_edit", args=[project.pk, turnover.pk])
    data = {
        "label": "LOCK",
        "received_date": "2026-11-01",
        "notes": "",
        **_management("reels", 1, 1),
        "reels-0-include": "on",
        "reels-0-tc_in": "1:00:00:00",
        "reels-0-tc_out": "1:20:00:00",
        **_management("new", 1),
        "new-0-name": "2",
        "new-0-tc_in": "2:00:00:00",
        "new-0-tc_out": "2:18:00:00",
    }
    response = editor.post(url, data)
    assert response["Location"] == reverse("cues:picture", args=[project.pk])
    assert sorted(
        turnover.picture_versions.values_list("picture__name", flat=True)
    ) == ["1", "2"]


def _fresh_turnover_plain(project: models.Project) -> models.Turnover:
    from cues.services import turnovers  # noqa: PLC0415 pylint: disable=import-outside-toplevel

    return turnovers.create_turnover(
        project,
        label="LOCK",
        received_date=datetime.date(2026, 11, 1),
        reels=[turnovers.ReelEntry(None, "1", 3600 * 24, 3600 * 24 + 1000)],
    )
