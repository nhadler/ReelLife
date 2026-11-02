"""The Schedule page and the facts it plans from."""

from collections.abc import Callable, Iterator
import datetime

from django.test import Client
from django.urls import reverse
import freezegun
import pytest

from accounts.models import Role
from cues import models
from cues.services import agenda
from cues.services import scheduler
from cues.services.scheduler import Fraction
from cues.views import schedule as schedule_view
from tests import factories

pytestmark = pytest.mark.django_db

MON = datetime.date(2026, 11, 2)
MINUTE = 24 * 60  # frames at 24 fps


@pytest.fixture(autouse=True)
def fixture_today() -> Iterator[None]:
    with freezegun.freeze_time("2026-11-02 18:00:00"):
        yield


def _status(project: models.Project, kind: str) -> models.StatusOption:
    return project.statuses.filter(kind=kind).first() or (
        factories.StatusOptionFactory.create(project=project, kind=kind)
    )


def _cue(
    project: models.Project,
    number: str,
    kind: str = models.StatusKind.TO_DO,
    minutes: int = 1,
    **fields: object,
) -> models.Cue:
    picture = project.pictures.first() or factories.PictureFactory.create(
        project=project, name="1"
    )
    values: dict[str, object] = {
        "project": project,
        "picture": picture,
        "number": number,
        "status": _status(project, kind),
        "tc_in": 86_400,
        "tc_out": 86_400 + minutes * MINUTE,
    }
    return factories.CueFactory.create(**(values | fields))


@pytest.fixture(name="film")
def fixture_film() -> models.Project:
    """Due Thursday, with a shared day off Tuesday and its own Wednesday."""
    film = factories.ProjectFactory.create(
        title="Night Ferry", production_number="NF", frame_rate="24"
    )
    factories.DeadlineFactory.create(project=film, end_date=MON + 3 * _DAY)
    models.DayOff.objects.create(date=MON + _DAY, note="Holiday")
    models.DayOff.objects.create(date=MON + 2 * _DAY, project=film)
    return film


_DAY = datetime.timedelta(days=1)


def test_build_plans_what_is_left_to_write(film: models.Project) -> None:
    easy = _cue(film, "1m01", difficulty=models.Difficulty.EASY)
    hard = _cue(film, "1m02", difficulty=models.Difficulty.HARD)
    revise = _cue(film, "1m03", models.StatusKind.REDO, difficulty="Moderate")
    started = _cue(film, "1m04", models.StatusKind.IN_PROGRESS)
    _cue(film, "1m05", models.StatusKind.DONE)
    _cue(film, "1m06", active=False)
    open_ended = _cue(film, "1m07", tc_out=None)
    archived = factories.ProjectFactory.create(archived=True)
    _cue(archived, "1m01")
    found = agenda.build(MON)
    parts = [part for day in found.plan.days for part in day.parts]
    assert {part.key for part in parts} == {
        easy.pk,
        hard.pk,
        revise.pk,
        started.pk,
    }
    factor = {part.key: part.effort / part.music for part in parts}
    # Hard 3x; a Moderate rewrite 2 x 0.5; no difficulty counts as Easy.
    assert factor == {easy.pk: 1, hard.pk: 3, revise.pk: 1, started.pk: 1}
    # Only Monday and Thursday are this film's working days.
    assert {day.date for day in found.plan.days} == {MON, MON + 3 * _DAY}
    assert [cue.number for cue in found.unschedulable] == [open_ended.number]
    assert found.days_off == {
        MON + _DAY: [(None, "Holiday")],
        MON + 2 * _DAY: [(film.pk, "")],
    }
    assert found.projects[film.pk].due == MON + 3 * _DAY
    assert found.cues[revise.pk].revise
    assert found.cues[easy.pk].url == f"/cues/{easy.pk}/"


def test_projects_at_once_follows_the_setting() -> None:
    near = factories.ProjectFactory.create(frame_rate="24")
    far = factories.ProjectFactory.create(frame_rate="24")
    factories.DeadlineFactory.create(project=near, end_date=MON + 4 * _DAY)
    factories.DeadlineFactory.create(project=far, end_date=MON + 30 * _DAY)
    for n in range(1, 4):
        _cue(near, f"1m0{n}", minutes=2)
        _cue(far, f"1m0{n}", minutes=2)

    def projects_on_monday() -> set[int]:
        found = agenda.build(MON)
        day = found.plan.day(MON)
        assert day is not None
        return {found.cues[part.key].project for part in day.parts}

    # In parallel (the default) both are written on the first day.
    assert projects_on_monday() == {near.pk, far.pk}
    settings = models.AppSettings.load()
    settings.schedule_projects = models.AppSettings.ProjectsAtOnce.ONE
    settings.save()
    # One at a time, the nearer deadline has the day to itself.
    assert projects_on_monday() == {near.pk}


def test_a_project_waits_for_its_start_date() -> None:
    later = factories.ProjectFactory.create(frame_rate="24")
    factories.DeadlineFactory.create(
        project=later, start_date=MON + 7 * _DAY, end_date=MON + 9 * _DAY
    )
    _cue(later, "1m01", minutes=3)
    found = agenda.build(MON)
    assert found.projects[later.pk].start == MON + 7 * _DAY
    # A minute a day over the three days from the start.
    assert [(day.date, day.music) for day in found.plan.days] == [
        (MON + offset * _DAY, Fraction(60)) for offset in (7, 8, 9)
    ]


@pytest.fixture(name="viewer")
def fixture_viewer(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.VIEWER)


def _shown(number: str) -> str:
    return f'cal-cue__number">{number}<'


def _page(client: Client, **query: str) -> str:
    response = client.get(reverse("cues:schedule"), query)
    assert response.status_code == 200
    return response.content.decode()


def test_the_month_and_week_show_each_days_cues(
    viewer: Client, film: models.Project
) -> None:
    _cue(film, "1m01", minutes=4, difficulty=models.Difficulty.HARD)
    month = _page(viewer)
    assert '<h2 class="schedule__title">November 2026</h2>' in month
    assert 'aria-current="page">Schedule</a>' in month
    assert _shown("1m01") in month
    # Four minutes over two days: two minutes each, shown as a share.
    assert "2:00 of 4:00" in month
    assert "Holiday" in month
    assert "Night Ferry off" in month
    assert "1 cue, 4:00 of music, done by" in month
    week = _page(viewer, view="week", week="2026-11-05")
    assert "Nov 1 – 7, 2026" in week  # weeks start Sunday (US dates)
    # Monday (today) is chosen: its cues listed beside the calendar.
    assert 'id="schedule-day-title">' in week
    assert "Night Ferry · Hard" in week
    assert 'aria-current="true"' in week
    # Bad dates fall back to now.
    assert "November 2026" in _page(viewer, month="soon")
    assert "Nov 1 – 7, 2026" in _page(viewer, view="week", week="x")
    assert "October 2026" in _page(viewer, month="2026-10")


def test_the_projects_list_picks_what_shows(
    viewer: Client, film: models.Project
) -> None:
    other = factories.ProjectFactory.create(title="Harbour Lights")
    factories.DeadlineFactory.create(project=other, end_date=MON + 20 * _DAY)
    _cue(film, "1m01")
    _cue(other, "2m01")
    every = _page(viewer)
    assert _shown("1m01") in every and _shown("2m01") in every
    # Each project is a ticked box, in its own tone.
    assert f'name="p" value="{film.pk}" checked' in every
    assert f"cal-project tone-{other.pk % 8}" in every
    response = viewer.get(
        reverse("cues:schedule"), {"f": "1", "p": [str(other.pk), "x", "999"]}
    )
    only = response.content.decode()
    assert _shown("2m01") in only and _shown("1m01") not in only
    assert f'name="p" value="{film.pk}">' in only  # unticked
    assert "Night Ferry off" not in only  # the other film's own day off
    assert f"f=1&amp;p={other.pk}" in only  # the arrows keep the choice
    # Ticking none shows none; without f, every project shows.
    assert "Nothing left to write." in _page(viewer, f="1")


def test_a_daily_maximum_too_low_says_what_it_misses(
    viewer: Client, film: models.Project
) -> None:
    settings = models.AppSettings.load()
    settings.schedule_daily_max = 30
    settings.save()
    _cue(film, "1m01", minutes=2)
    page = _page(viewer)
    assert "At your daily maximum, Night Ferry misses" in page
    assert "it would finish" in page


def test_work_beyond_three_years(viewer: Client, film: models.Project) -> None:
    settings = models.AppSettings.load()
    settings.schedule_daily_max = 1
    settings.schedule_min_part = 1
    settings.save()
    _cue(film, "1m01", minutes=60, difficulty=models.Difficulty.HARD)
    page = _page(viewer)
    assert "finish in the next three years." in page
    assert "not planned" in page


def test_without_deadlines_nothing_is_planned(viewer: Client) -> None:
    film = factories.ProjectFactory.create()
    _cue(film, "1m01")
    page = _page(viewer)
    assert "no project has a composition deadline ahead" in page
    assert "not planned" in page


def test_nothing_left_to_write(viewer: Client, film: models.Project) -> None:
    _cue(film, "1m01", models.StatusKind.DONE)
    page = _page(viewer)
    assert "Nothing left to write." in page
    assert "<select" not in page  # no projects to filter by


def test_a_whole_cue_shows_its_music_alone() -> None:
    cue = agenda.CueInfo(1, 1, "1m01", "", "", "/", "", revise=False)
    film = agenda.ProjectInfo(1, "Night Ferry", "NF", None)
    whole = scheduler.Part(1, Fraction(90), Fraction(90), Fraction(90))
    share = scheduler.Part(1, Fraction(30), Fraction(30), Fraction(90))
    assert schedule_view.PartView(cue, film, whole).music == "1:30"
    assert schedule_view.PartView(cue, film, share).music == "0:30 of 1:30"


def test_choosing_a_day_lists_its_work(
    viewer: Client, film: models.Project
) -> None:
    _cue(film, "1m01", minutes=1)
    _cue(film, "1m02", tc_out=None)
    thursday = _page(viewer, day="2026-11-05")
    assert 'href="?month=2026-11&amp;day=2026-11-05"' in thursday
    assert "Thu Nov 5" in thursday
    assert 'class="day-total"' in thursday  # it has work
    assert "Holiday" in _page(viewer, day="2026-11-03")  # a day off
    assert "Nothing was planned." in _page(viewer, day="2026-11-01")
    assert "Nothing planned." in _page(viewer, day="2026-11-20")
    # A day outside the month shown falls back to today.
    assert "Mon Nov 2" in _page(viewer, month="2026-11", day="2027-01-01")
    # Another month starts on its first day.
    assert "Tue Dec 1" in _page(viewer, month="2026-12")
    # Cues without an IN or OUT, as a callout.
    assert "1 cue without an IN or OUT" in thursday
