"""The stats panel: figures, deadline choice, access."""

from collections.abc import Callable
import datetime
import json

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues import timecode
from cues.services import history
from cues.services import stats
from cues.services import summaries
from tests import factories
from tests.medium.test_cue_tools import _cue
from tests.medium.test_cue_tools import _reel

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 11, 2)


def _status(project: models.Project, kind: str) -> models.StatusOption:
    status = project.statuses.filter(kind=kind).first()
    assert status is not None
    return status


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    """Four 30 s cues: to write, composed, approved, a song; plus an
    omitted cue, an inactive one and one without times."""
    project = factories.ProjectFactory.create()
    reel = _reel(project, "1")
    kinds = [
        models.StatusKind.TO_DO,
        models.StatusKind.DONE,
        models.StatusKind.APPROVED,
        models.StatusKind.SONG,
        models.StatusKind.OMIT,
    ]
    for i, kind in enumerate(kinds):
        cue = _cue(reel, f"1m0{i + 1}", i * 60)
        cue.status = _status(project, kind)
        cue.save()
    inactive = _cue(reel, "1m06", 400)
    inactive.active = False
    inactive.save()
    untimed = _cue(reel, "1m07", 500)
    untimed.tc_out = None
    untimed.save()
    return project


def _row(rows: list[stats.Row], label: str) -> stats.Row:
    return next(row for row in rows if row.label == label)


def test_totals_and_progress(project: models.Project) -> None:
    figures = stats.for_project(project, TODAY)
    total = _row(figures.totals, "Total")
    assert (total.cues, total.duration, total.percent) == (5, "2:00", 100)
    compositions = _row(figures.totals, "Compositions")
    assert (compositions.cues, compositions.percent) == (4, 75)
    assert _row(figures.totals, "Original Songs").cues == 1
    licensed = _row(figures.totals, "Licensed")
    assert (licensed.cues, licensed.values) == (0, ())
    composed = _row(figures.progress, "Composed")
    assert (composed.cues, composed.percent) == (2, 67)
    assert _row(figures.progress, "Remaining").cues == 2
    assert _row(figures.progress, "To Revise").cues == 0
    assert _row(figures.progress, "Approved").cues == 1
    assert _row(figures.progress, "Unapproved").cues == 3
    song = _status(project, models.StatusKind.SONG)
    assert _row(figures.totals, "Original Songs").values == (str(song.pk),)
    assert _row(figures.totals, "Original Songs").filter_values == str(song.pk)


def test_status_type_and_options(project: models.Project) -> None:
    cue = project.cues.get(number="1m01")
    sub = factories.SubStatusFactory.create(project=project)
    version = cue.current_version
    assert version is not None
    version.sub_status = sub
    version.save()
    theme = models.ThemeOption.objects.create(
        project=project, name="Sea", color="#336699"
    )
    models.VersionTheme.objects.create(version=version, theme=theme)
    instrument = models.InstrumentOption.objects.create(
        project=project, name="Cello", color="#996633"
    )
    models.CueInstrument.objects.create(cue=cue, instrument=instrument)
    models.ThemeOption.objects.create(
        project=project, name="Unused", color="#000000"
    )
    figures = stats.for_project(project, TODAY)
    omit = _row(figures.status, _status(project, models.StatusKind.OMIT).label)
    assert omit.cues == 1  # Status covers every active cue
    assert omit.color
    assert [row.label for row in figures.cue_type] == ["Score"]
    assert _row(figures.cue_type, "Score").values == ("SCORE",)
    assert [row.label for row in figures.sub_status] == [sub.label]
    assert [(r.label, r.values) for r in figures.themes] == [
        ("Sea", (str(theme.pk),))
    ]
    assert [r.label for r in figures.instrumentation] == ["Cello"]
    whole = _row(figures.themes, "Sea")
    assert whole.duration == _row(figures.instrumentation, "Cello").duration
    assert dict(figures.details) == {
        "Runtime": "20:00",
        "Frame Rate": "23.976",
        "Reels": "1",
    }


def test_an_empty_project() -> None:
    project = factories.ProjectFactory.create()
    figures = stats.for_project(project, TODAY)
    assert _row(figures.totals, "Total").percent == 0
    assert not figures.cue_type
    assert figures.pace is None
    assert figures.pace_label == ""


def test_untimed_cues_count_without_time(project: models.Project) -> None:
    figures = stats.for_project(project, TODAY)
    to_write = _row(
        figures.status, _status(project, models.StatusKind.TO_DO).label
    )
    assert (to_write.cues, to_write.duration) == (2, "0:30")


def test_cues_without_a_status() -> None:
    project = factories.ProjectFactory.create()
    _cue(_reel(project, "1"), "1m01", 0).__class__.objects.update(status=None)
    figures = stats.for_project(project, TODAY)
    assert _row(figures.totals, "Compositions").values == ("",)


def test_deadlines_and_the_pace_deadline(project: models.Project) -> None:
    passed = factories.DeadlineFactory.create(
        project=project,
        kind=models.Deadline.Kind.COMPOSITION,
        end_date=TODAY - datetime.timedelta(days=1),
    )
    near = factories.DeadlineFactory.create(
        project=project,
        kind=models.Deadline.Kind.COMPOSITION,
        end_date=TODAY + datetime.timedelta(days=9),
    )
    later = factories.DeadlineFactory.create(
        project=project,
        kind=models.Deadline.Kind.MIX,
        end_date=TODAY + datetime.timedelta(days=19),
    )
    figures = stats.for_project(project, TODAY)
    assert [(d.pk, d.chosen, d.passed) for d in figures.deadlines] == [
        (passed.pk, False, True),
        (near.pk, True, False),
        (later.pk, False, False),
    ]
    assert figures.pace is not None
    assert figures.pace.due == near.end_date
    assert figures.pace_label == str(near)
    # A chosen deadline drives the pace while it is ahead.
    project.pace_deadline = later
    project.save()
    assert summaries.pace_deadline(project, TODAY) == later
    figures = stats.for_project(project, TODAY)
    assert figures.pace is not None
    assert figures.pace.due == later.end_date
    assert figures.pace.total == summaries.clock(
        figures.pace.write_seconds + figures.pace.revise_seconds
    )
    # Once passed, the next composition deadline takes over again.
    project.pace_deadline = passed
    project.save()
    assert summaries.pace_deadline(project, TODAY) == near


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def test_the_panel(editor: Client, project: models.Project) -> None:
    factories.DeadlineFactory.create(
        project=project,
        kind=models.Deadline.Kind.COMPOSITION,
        end_date=datetime.date.today() + datetime.timedelta(days=5),
    )
    url = reverse("cues:stats_panel", args=[project.pk])
    html = editor.get(url).content.decode()
    for heading in ("Totals", "Progress", "Per Day", "Deadlines", "Status"):
        assert f">{heading}" in html
    assert "data-stat-select" in html
    assert "data-stat-filter" in html
    assert "data-side-panel-close" in html
    assert 'hx-trigger="cues:changed from:body"' in html
    assert "working day" in html
    assert 'type="radio" name="deadline"' in html
    assert (
        " disabled" not in html.split("stats__deadlines")[1].split("</form>")[0]
    )


def test_the_panel_without_deadlines(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    html = editor.get(
        reverse("cues:stats_panel", args=[project.pk])
    ).content.decode()
    assert "No deadline ahead." in html
    assert "None yet." in html


def test_choosing_the_pace_deadline(
    editor: Client, project: models.Project
) -> None:
    deadline = factories.DeadlineFactory.create(
        project=project, end_date=datetime.date.today()
    )
    url = reverse("cues:stats_deadline", args=[project.pk])
    response = editor.post(url, {"deadline": deadline.pk})
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"]) == {"cues:changed": True}
    project.refresh_from_db()
    assert project.pace_deadline == deadline
    count = models.ChangeSet.objects.count()
    editor.post(url, {"deadline": deadline.pk})  # the same: no change set
    assert models.ChangeSet.objects.count() == count
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    project.refresh_from_db()
    assert project.pace_deadline is None
    other = factories.DeadlineFactory.create()
    assert editor.post(url, {"deadline": other.pk}).status_code == 404
    assert editor.post(url, {}).status_code == 404
    html = editor.get(
        reverse("cues:stats_panel", args=[project.pk])
    ).content.decode()
    assert "today" in html


def test_viewers_read_but_cannot_choose(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    deadline = factories.DeadlineFactory.create(
        project=project,
        end_date=datetime.date.today() + datetime.timedelta(days=3),
    )
    viewer = role_client(Role.VIEWER)
    html = viewer.get(
        reverse("cues:stats_panel", args=[project.pk])
    ).content.decode()
    assert "disabled" in html
    response = viewer.post(
        reverse("cues:stats_deadline", args=[project.pk]),
        {"deadline": deadline.pk},
    )
    assert response.status_code == 403


def test_the_overview_has_the_stats_button(
    editor: Client, project: models.Project
) -> None:
    html = editor.get(reverse("cues:project", args=[project.pk])).content
    assert b'id="stats-toggle"' in html
    assert reverse("cues:stats_panel", args=[project.pk]).encode() in html


def test_themes_and_instruments_count_their_own_seconds(
    project: models.Project,
) -> None:
    cue = project.cues.get(number="1m01")
    version = cue.current_version
    assert version is not None
    theme = models.ThemeOption.objects.create(
        project=project, name="Sea", color="#336699"
    )
    models.VersionTheme.objects.create(version=version, theme=theme, seconds=75)
    instrument = models.InstrumentOption.objects.create(
        project=project, name="Cello", color="#996633"
    )
    models.CueInstrument.objects.create(
        cue=cue, instrument=instrument, seconds=None
    )
    figures = stats.for_project(project, TODAY)
    assert _row(figures.themes, "Sea").duration == "1:15"
    # No seconds: the whole cue.
    cello = _row(figures.instrumentation, "Cello")
    assert cello.duration == timecode.format_duration(
        cue.duration or 0, project.rate
    )


def test_the_breakdown_splits_the_music() -> None:
    project = factories.ProjectFactory.create(frame_rate="24")
    picture = factories.PictureFactory.create(project=project)
    kinds = {s.kind: s for s in project.statuses.all()}
    for number, cue_type, kind, seconds in (
        ("1m01", "SCORE", models.StatusKind.APPROVED, 60),
        ("1m02", "SCORE", models.StatusKind.TO_DO, 90),
        ("1m03", "SOURCE", models.StatusKind.LICENSE, 30),
    ):
        factories.CueFactory.create(
            project=project,
            picture=picture,
            number=number,
            cue_type=cue_type,
            status=kinds[kind],
            tc_in=0,
            tc_out=seconds * 24,
        )
    result = summaries.for_project(project).breakdown
    assert result is not None
    # The licensed source cue is outside the total (compositions only).
    assert result.by_type == [("Score", "2:30")]
    assert result.licensed == "0:30"
    assert [(label, duration) for label, _, duration in result.parts] == [
        ("Approved", "1:00"),
        ("To Write", "1:30"),
    ]
    assert (result.total, result.cues, result.remaining) == ("2:30", 2, "1:30")
