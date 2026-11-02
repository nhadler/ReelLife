"""The daily writing pace, excluding days off."""

from collections.abc import Callable
import datetime

from django.test import Client
from django.urls import reverse
from django.utils import timezone
import pytest

from accounts.models import Role
from cues import models
from cues.services import summaries
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 11, 1)


def _project(rate: str = "24") -> models.Project:
    return factories.ProjectFactory.create(frame_rate=rate)


def _cue(
    project: models.Project, status: str, seconds: int, **extra: object
) -> None:
    factories.CueFactory.create(
        project=project,
        picture=factories.PictureFactory.create(project=project),
        status=project.statuses.get(label=status),
        tc_in=86_400,
        tc_out=86_400 + seconds * 24,
        **extra,
    )


def _deadline(
    project: models.Project, end: datetime.date, kind: str = "composition"
) -> None:
    factories.DeadlineFactory.create(project=project, kind=kind, end_date=end)


def test_pace_spreads_what_is_left_over_the_days() -> None:
    project = _project()
    _deadline(project, TODAY + datetime.timedelta(days=9))  # 10 days
    _cue(project, "To Write", 600)
    _cue(project, "In Progress", 400)
    _cue(project, "Revisions", 300)
    _cue(project, "Rewrite", 100)
    _cue(project, "Composed", 900)  # done
    _cue(project, "To Write", 500, active=False)  # left out
    pace = summaries.pace(project, TODAY)
    assert pace is not None
    assert (pace.days, pace.calendar_days) == (10, 10)
    assert (pace.write_seconds, pace.revise_seconds) == (100, 40)
    assert (pace.write, pace.revise) == ("1:40", "0:40")


def test_a_start_date_ahead_counts_the_days_from_it() -> None:
    project = _project()
    factories.DeadlineFactory.create(
        project=project,
        kind="composition",
        start_date=TODAY + datetime.timedelta(days=5),
        end_date=TODAY + datetime.timedelta(days=9),  # 5 days from the start
    )
    models.DayOff.objects.create(date=TODAY + datetime.timedelta(days=2))
    _cue(project, "To Write", 500)
    pace = summaries.pace(project, TODAY)
    assert pace is not None
    assert (pace.days, pace.calendar_days) == (5, 5)  # the day off is before
    assert pace.write_seconds == 100
    started = summaries.pace(project, TODAY + datetime.timedelta(days=7))
    assert started is not None
    assert started.days == 3  # from today once the start has passed


def test_days_off_are_left_out_and_seconds_round_up() -> None:
    project = _project("23.976")
    _deadline(project, TODAY + datetime.timedelta(days=3))  # 4 days
    models.DayOff.objects.create(date=TODAY + datetime.timedelta(days=1))
    models.DayOff.objects.create(date=TODAY + datetime.timedelta(days=30))
    _cue(project, "To Write", 100)
    pace = summaries.pace(project, TODAY)
    assert pace is not None
    assert (pace.days, pace.calendar_days) == (3, 4)
    assert pace.write_seconds == 34  # 100 / 3, rounded up


def test_a_projects_own_days_off_count_only_for_it() -> None:
    project, other = _project(), _project()
    _deadline(project, TODAY + datetime.timedelta(days=4))  # 5 days
    shared = TODAY + datetime.timedelta(days=1)
    models.DayOff.objects.create(date=shared)
    models.DayOff.objects.create(project=project, date=shared)  # once
    models.DayOff.objects.create(
        project=project, date=TODAY + datetime.timedelta(days=2)
    )
    models.DayOff.objects.create(
        project=other, date=TODAY + datetime.timedelta(days=3)
    )
    _cue(project, "To Write", 90)
    pace = summaries.pace(project, TODAY)
    assert pace is not None
    assert (pace.days, pace.calendar_days) == (3, 5)


def test_no_days_left_puts_it_all_on_today() -> None:
    project = _project()
    _deadline(project, TODAY)
    models.DayOff.objects.create(date=TODAY)
    _cue(project, "To Write", 90)
    pace = summaries.pace(project, TODAY)
    assert pace is not None and (pace.days, pace.write_seconds) == (0, 90)


def test_only_upcoming_composition_deadlines_count() -> None:
    project = _project()
    _deadline(project, TODAY - datetime.timedelta(days=1))
    _deadline(project, TODAY + datetime.timedelta(days=5), kind="mix")
    assert summaries.pace(project, TODAY) is None
    _deadline(project, TODAY + datetime.timedelta(days=20))
    _deadline(project, TODAY + datetime.timedelta(days=10))
    pace = summaries.pace(project, TODAY)
    assert pace is not None and pace.due == TODAY + datetime.timedelta(days=10)


def test_cues_without_timecodes_add_nothing() -> None:
    project = _project()
    _deadline(project, TODAY)
    factories.CueFactory.create(
        project=project,
        picture=factories.PictureFactory.create(project=project),
        tc_in=None,
        tc_out=None,
    )
    pace = summaries.pace(project, TODAY)
    assert pace is not None and pace.write_seconds == 0


def test_clock() -> None:
    assert summaries.clock(59) == "0:59"
    assert summaries.clock(3_725) == "1:02:05"
    assert summaries.clock(-5) == "0:00"


def test_total_needs_two_projects() -> None:
    first, second = _project(), _project()
    for project, seconds in ((first, 100), (second, 50)):
        _deadline(project, TODAY)
        _cue(project, "To Write", seconds)
        _cue(project, "Revisions", seconds)
    found = [summaries.for_project(p, TODAY) for p in (first, second)]
    assert summaries.total_pace(found) == ("2:30", "2:30")
    assert summaries.total_pace(found[:1]) is None


def test_header_and_dashboard_show_the_pace(
    role_client: Callable[[Role], Client], settings: object
) -> None:
    del settings
    client = role_client(Role.EDITOR)
    today = timezone.localdate()
    first, second = _project(), _project()
    for project in (first, second):
        _deadline(project, today + datetime.timedelta(days=1))
        _cue(project, "To Write", 120)
    models.DayOff.objects.create(date=today)
    html = client.get(reverse("cues:project", args=[first.pk])).content.decode()
    text = " ".join(html.split())
    # Today is a day off, so one day is left to work (in the tooltip).
    assert "Per day <strong>2:00</strong> write</span>" in text
    assert 'title="1 working day to' in text
    html = client.get(reverse("cues:dashboard")).content.decode()
    assert "write <strong>4:00</strong> and revise" in html  # all projects
    assert '<span class="project__big">2:00</span>' in html  # each one


def test_nothing_left_shows_no_pace(
    role_client: Callable[[Role], Client],
) -> None:
    project = _project()
    _deadline(project, timezone.localdate())
    _cue(project, "Composed", 60)
    html = (
        role_client(Role.EDITOR)
        .get(reverse("cues:project", args=[project.pk]))
        .content.decode()
    )
    assert "a day" not in html
