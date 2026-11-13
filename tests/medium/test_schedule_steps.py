"""Pins ("Schedule On…") and saved daily plans (the Schedule, steps 2-3)."""

from collections.abc import Callable, Iterator
import datetime
from decimal import Decimal
import json

from django.test import Client
from django.urls import reverse
import freezegun
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import agenda
from cues.services import history
from cues.services import writing
from tests import factories

pytestmark = pytest.mark.django_db

MON = datetime.date(2026, 11, 2)
_DAY = datetime.timedelta(days=1)
MINUTE = 24 * 60  # frames at 24 fps
HX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def fixture_today() -> Iterator[None]:
    with freezegun.freeze_time("2026-11-02 18:00:00"):
        yield


@pytest.fixture(name="film")
def fixture_film() -> models.Project:
    film = factories.ProjectFactory.create(title="Night Ferry", frame_rate="24")
    factories.DeadlineFactory.create(project=film, end_date=MON + 9 * _DAY)
    return film


def _cue(
    film: models.Project,
    number: str,
    minutes: int,
    kind: str = models.StatusKind.TO_DO,
) -> models.Cue:
    picture = film.pictures.first() or factories.PictureFactory.create(
        project=film, name="1"
    )
    status = film.statuses.filter(kind=kind).first()
    return factories.CueFactory.create(
        project=film,
        picture=picture,
        number=number,
        status=status
        or factories.StatusOptionFactory.create(project=film, kind=kind),
        tc_in=86_400,
        tc_out=86_400 + minutes * MINUTE,
    )


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


# Pins


def test_a_pinned_cue_sits_on_its_day(film: models.Project) -> None:
    first = _cue(film, "1m01", 2)
    later = _cue(film, "1m02", 2)
    models.SchedulePin.objects.create(cue=later, date=MON + 5 * _DAY)
    plan = agenda.build(MON, save=False).plan
    day = plan.day(MON + 5 * _DAY)
    assert day is not None
    assert [(p.key, p.pinned) for p in day.parts] == [(later.pk, True)]
    assert all(
        later.pk not in {p.key for p in d.parts}
        for d in plan.days
        if d.date != MON + 5 * _DAY
    )
    assert first.pk in {p.key for d in plan.days for p in d.parts}


def test_schedule_on_pins_unpins_and_undoes(
    editor: Client, film: models.Project
) -> None:
    cue = _cue(film, "1m01", 2)
    url = reverse("cues:schedule_on", args=[cue.pk])
    form = editor.get(url, headers=HX).content.decode()
    assert "Schedule On: 1m01" in form and 'type="date"' in form
    assert "Unpin" not in form
    response = editor.post(url, {"date": "2026-11-05"}, headers=HX)
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"]) == {
        "dialog:close": True,
        "schedule:changed": True,
    }
    assert models.SchedulePin.objects.get(cue=cue).date == MON + 3 * _DAY
    change = models.ChangeSet.objects.filter(project=film).latest("pk")
    assert change.label == "Schedule 1m01 on Nov 5"
    assert "Unpin" in editor.get(url, headers=HX).content.decode()
    # Moving it is one more change; undoing that puts it back.
    editor.post(url, {"date": "2026-11-09"}, headers=HX)
    history.undo(models.ChangeSet.objects.filter(project=film).latest("pk"))
    assert models.SchedulePin.objects.get(cue=cue).date == MON + 3 * _DAY
    assert editor.post(url, {"unpin": "1"}, headers=HX).status_code == 204
    assert not models.SchedulePin.objects.filter(cue=cue).exists()
    latest = models.ChangeSet.objects.filter(project=film).latest("pk")
    assert latest.label == "Unschedule 1m01"
    # Unpinning a cue with no pin does nothing.
    count = models.ChangeSet.objects.count()
    assert editor.post(url, {"unpin": "1"}, headers=HX).status_code == 204
    assert models.ChangeSet.objects.count() == count


def test_schedule_on_refuses_the_past_and_viewers(
    editor: Client,
    film: models.Project,
    role_client: Callable[[Role], Client],
) -> None:
    cue = _cue(film, "1m01", 2)
    url = reverse("cues:schedule_on", args=[cue.pk])
    page = editor.post(url, {"date": "2026-11-01"}, headers=HX)
    assert "Pick today or a later day." in page.content.decode()
    assert not models.SchedulePin.objects.exists()
    assert editor.get(url)["Location"] == reverse("cues:cue", args=[cue.pk])
    viewer = role_client(Role.VIEWER)
    assert viewer.post(url, {"date": "2026-11-05"}).status_code == 403


def test_pins_show_on_the_schedule(
    editor: Client, film: models.Project
) -> None:
    cue = _cue(film, "1m01", 2)
    models.SchedulePin.objects.create(cue=cue, date=MON)
    page = editor.get(reverse("cues:schedule")).content.decode()
    assert ", scheduled on this day" in page
    assert reverse("cues:schedule_on", args=[cue.pk]) in page


def test_reset_schedule_unpins_the_project_and_undoes(
    editor: Client, film: models.Project
) -> None:
    first = _cue(film, "1m01", 2)
    second = _cue(film, "1m02", 2)
    other = factories.ProjectFactory.create(title="Other")
    elsewhere = factories.CueFactory.create(project=other)
    for cue in (first, second, elsewhere):
        models.SchedulePin.objects.create(cue=cue, date=MON + _DAY)
    models.WritingLog.objects.create(cue=first, date=MON, seconds=30)
    url = reverse("cues:schedule_reset", args=[film.pk])
    back = reverse("cues:project", args=[film.pk])
    response = editor.post(url, {"next": back}, follow=True)
    assert response.redirect_chain == [(back, 302)]
    assert "Reset Night Ferry&#x27;s schedule: unpinned 2 cues." in (
        response.content.decode()
    )
    assert list(models.SchedulePin.objects.all()) == [elsewhere.pin]
    assert models.WritingLog.objects.filter(cue=first).exists()
    change = models.ChangeSet.objects.filter(project=film).latest("pk")
    assert change.label == "Reset Schedule"
    history.undo(change)
    assert models.SchedulePin.objects.count() == 3


def test_reset_schedule_with_nothing_pinned_changes_nothing(
    editor: Client, film: models.Project
) -> None:
    _cue(film, "1m01", 2)
    count = models.ChangeSet.objects.count()
    url = reverse("cues:schedule_reset", args=[film.pk])
    # Another site's address isn't followed; the Schedule shows the film.
    response = editor.post(url, {"next": "https://evil.example/"})
    assert response["Location"] == (
        reverse("cues:schedule") + f"?f=1&p={film.pk}"
    )
    page = editor.get(response["Location"]).content.decode()
    assert "No cues in Night Ferry were pinned." in page
    assert models.ChangeSet.objects.count() == count


def test_reset_schedule_is_for_editors_and_posts_only(
    editor: Client,
    film: models.Project,
    role_client: Callable[[Role], Client],
) -> None:
    cue = _cue(film, "1m01", 2)
    models.SchedulePin.objects.create(cue=cue, date=MON)
    url = reverse("cues:schedule_reset", args=[film.pk])
    viewer = role_client(Role.VIEWER)
    assert viewer.post(url).status_code == 403
    assert editor.get(url).status_code == 405
    assert models.SchedulePin.objects.exists()
    project_page = reverse("cues:project", args=[film.pk])
    assert url in editor.get(project_page).content.decode()
    assert url not in viewer.get(project_page).content.decode()


def test_the_schedule_offers_reset_for_one_shown_project(
    editor: Client,
    film: models.Project,
    role_client: Callable[[Role], Client],
) -> None:
    _cue(film, "1m01", 2)
    url = reverse("cues:schedule_reset", args=[film.pk])
    schedule = reverse("cues:schedule")
    assert url not in editor.get(schedule).content.decode()
    one = f"{schedule}?f=1&p={film.pk}"
    assert url in editor.get(one).content.decode()
    assert url not in role_client(Role.VIEWER).get(one).content.decode()


# Saved daily plans


def test_the_first_look_each_day_saves_its_plan(film: models.Project) -> None:
    cue = _cue(film, "1m01", 20)
    agenda.build(MON)
    saved = models.ScheduleEntry.objects.get()
    assert (saved.date, saved.cue_id, saved.revise) == (MON, cue.pk, False)
    assert saved.music == saved.effort > 0
    # A later look the same day keeps the first plan.
    assert cue.tc_out is not None
    cue.tc_out += 10 * MINUTE
    cue.save()
    agenda.build(MON)
    assert models.ScheduleEntry.objects.get().music == saved.music
    # A look that saves nothing doesn't save.
    assert agenda.build(MON + _DAY, save=False)
    assert models.ScheduleEntry.objects.count() == 1


def test_logged_work_comes_off_what_is_left(film: models.Project) -> None:
    cue = _cue(film, "1m01", 20, models.StatusKind.IN_PROGRESS)
    models.WritingLog.objects.create(cue=cue, date=MON - 2 * _DAY, seconds=360)
    models.WritingLog.objects.create(cue=cue, date=MON, seconds=180)
    # Planned on a past day but not logged: that counts for nothing.
    models.ScheduleEntry.objects.create(
        date=MON - _DAY, cue=cue, music=Decimal(600), effort=Decimal(600)
    )
    found = agenda.build(MON, save=False)
    parts = [p for d in found.plan.days for p in d.parts if p.key == cue.pk]
    # 20 minutes, 9 logged: 11 left.
    assert sum(p.music for p in parts) == 11 * 60
    assert found.written == {MON - 2 * _DAY: {cue.pk: 360}, MON: {cue.pk: 180}}


def test_writing_logs_dont_count_toward_revisions(
    film: models.Project,
) -> None:
    cue = _cue(film, "1m01", 20, models.StatusKind.REDO)
    models.WritingLog.objects.create(cue=cue, date=MON - _DAY, seconds=600)
    found = agenda.build(MON, save=False)
    left = sum(p.music for d in found.plan.days for p in d.parts)
    assert left == 20 * 60


def test_logging_always_leaves_a_minimum_part(film: models.Project) -> None:
    cue = _cue(film, "1m01", 5, models.StatusKind.IN_PROGRESS)
    models.WritingLog.objects.create(cue=cue, date=MON - _DAY, seconds=600)
    found = agenda.build(MON, save=False)
    left = sum(p.music for d in found.plan.days for p in d.parts)
    assert left == 300  # the whole 5:00 is under the 10:00 minimum part


def test_past_days_show_cues_finished_since(
    editor: Client, film: models.Project
) -> None:
    done = _cue(film, "1m09", 2, models.StatusKind.DONE)
    models.ScheduleEntry.objects.create(
        date=MON - _DAY, cue=done, music=Decimal(120), effort=Decimal(120)
    )
    found = agenda.build(MON, save=False)
    assert found.cues[done.pk].number == "1m09"
    page = editor.get(
        reverse("cues:schedule"), {"day": (MON - _DAY).isoformat()}
    ).content.decode()
    assert "of music planned" in page and "1m09" in page


def test_nightly_saves_and_prunes(film: models.Project) -> None:
    cue = _cue(film, "1m01", 2)
    models.ScheduleEntry.objects.create(
        date=MON - 400 * _DAY, cue=cue, music=Decimal(1), effort=Decimal(1)
    )
    assert agenda.nightly(MON) == 1
    assert list(
        models.ScheduleEntry.objects.values_list("date", flat=True)
    ) == [MON]


def test_the_records_read_as_text(
    film: models.Project, role_client: Callable[[Role], Client]
) -> None:
    cue = _cue(film, "1m01", 2)
    pin = models.SchedulePin.objects.create(cue=cue, date=MON)
    entry = models.ScheduleEntry.objects.create(
        date=MON, cue=cue, music=Decimal(1), effort=Decimal(1)
    )
    role_client(Role.VIEWER)
    feed = models.CalendarFeed.objects.create(
        user=User.objects.get(), token_hash="x" * 64
    )
    log = models.WritingLog.objects.create(cue=cue, date=MON, seconds=80)
    assert str(log) == f"{cue} on 2026-11-02: 80 s"
    assert str(pin) == f"{cue} on 2026-11-02"
    assert str(entry) == f"2026-11-02 cue {cue.pk}"
    assert str(feed).startswith("Calendar feed for ")


# Logging what was written


def test_log_written_logs_stretches_and_carries_on(
    editor: Client, film: models.Project
) -> None:
    cue = _cue(film, "1m01", 10, models.StatusKind.IN_PROGRESS)
    url = reverse("cues:log_written", args=[cue.pk])
    # From the cue's IN to its OUT, or a planned share from the Schedule.
    form = editor.get(url, {"date": "2026-11-01"}, headers=HX).content
    assert b'value="1:00:00:00"' in form and b'value="1:10:00:00"' in form
    assert b"Logged" not in form
    shared = editor.get(url, {"amount": "2:30"}, headers=HX).content
    assert b'value="1:02:30:00"' in shared
    editor.post(
        url,
        {"date": "2026-11-01", "start": "1:00:00:00", "end": "1:02:30:00"},
        headers=HX,
    )
    log = models.WritingLog.objects.get()
    assert (log.date, log.seconds, log.start, log.end, log.revise) == (
        MON - _DAY,
        150,
        0,
        150 * 24,
        False,
    )
    latest = models.ChangeSet.objects.filter(project=film).latest("pk")
    assert latest.label == "Log 1:00:00:00 to 1:02:30:00 of 1m01 on Nov 1"
    # The next log starts where that one ended, and lists it.
    again = editor.get(url, {"date": "2026-11-01"}, headers=HX).content
    assert b'value="1:02:30:00"' in again
    assert b"1:00:00:00 to 1:02:30:00" in again and b"Remove" in again
    # Several a day; an overlap counts once.
    for stretch in (("1:02:30:00", "1:05:00:00"), ("1:04:00:00", "1:06:00:00")):
        editor.post(
            url,
            {"date": "2026-11-01", "start": stretch[0], "end": stretch[1]},
            headers=HX,
        )
    assert models.WritingLog.objects.count() == 3
    assert writing.logged([cue.pk], film.rate) == {(cue.pk, False): 360}
    assert writing.next_start(cue) == 6 * 60 * 24
    # Remove takes off one log and keeps the dialog open; Undo puts it back.
    response = editor.post(url, {"remove": str(log.pk)}, headers=HX)
    assert response.status_code == 200
    assert json.loads(response["HX-Trigger"]) == {"schedule:changed": True}
    assert b"Log Written: 1m01" in response.content
    assert models.WritingLog.objects.count() == 2
    history.undo(models.ChangeSet.objects.filter(project=film).latest("pk"))
    assert models.WritingLog.objects.count() == 3
    # Removing a log that isn't there does nothing.
    count = models.ChangeSet.objects.count()
    for missing in ("999999", "x"):
        editor.post(url, {"remove": missing}, headers=HX)
    assert models.ChangeSet.objects.count() == count


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (
            {"date": "2026-11-03", "start": "1:00:00:00", "end": "1:01:00:00"},
            "today or an earlier day",
        ),
        (
            {"date": "2026-11-02", "start": "lots", "end": "1:01:00:00"},
            "timecode",
        ),
        (
            {"date": "2026-11-02", "start": "1:02:00:00", "end": "1:01:00:00"},
            "From before To",
        ),
        (
            {"date": "2026-11-02", "start": "1:09:00:00", "end": "1:12:00:00"},
            "Pick a stretch from 1:00:00:00 to 1:10:00:00",
        ),
    ],
)
def test_log_written_refuses(
    editor: Client, film: models.Project, data: dict[str, str], error: str
) -> None:
    cue = _cue(film, "1m01", 10)
    url = reverse("cues:log_written", args=[cue.pk])
    page = editor.post(url, data, headers=HX).content.decode()
    assert error in page
    assert not models.WritingLog.objects.exists()


def test_log_written_needs_in_and_out(
    editor: Client, film: models.Project
) -> None:
    cue = _cue(film, "1m01", 10)
    cue.tc_in = cue.tc_out = None
    cue.save(update_fields=["tc_in", "tc_out"])
    url = reverse("cues:log_written", args=[cue.pk])
    page = editor.get(url, headers=HX).content.decode()
    assert "Give 1m01 an IN and OUT to log what's written." in page
    assert 'name="start"' not in page
    editor.post(url, {"start": "1:00:00:00", "end": "1:01:00:00"}, headers=HX)
    assert not models.WritingLog.objects.exists()
    with pytest.raises(writing.StretchError):
        writing.log_stretch(cue, MON, 0, 24, None)


def test_log_written_starts_again_for_revisions_and_after_out(
    editor: Client, film: models.Project
) -> None:
    cue = _cue(film, "1m01", 2, models.StatusKind.IN_PROGRESS)
    url = reverse("cues:log_written", args=[cue.pk])
    editor.post(
        url,
        {"date": "2026-11-02", "start": "1:00:00:00", "end": "1:02:00:00"},
        headers=HX,
    )
    # Logged to OUT: the next log starts from IN again.
    assert b'value="1:00:00:00"' in editor.get(url, headers=HX).content
    # In revisions, revision logs start from IN, apart from the writing.
    cue.status = film.statuses.filter(kind=models.StatusKind.REDO).first() or (
        factories.StatusOptionFactory.create(
            project=film, kind=models.StatusKind.REDO
        )
    )
    cue.save(update_fields=["status"])
    page = editor.get(url, headers=HX).content
    assert b'value="1:00:00:00"' in page and b"Logged" not in page
    with pytest.raises(writing.StretchError):
        writing.log_stretch(cue, MON, 24, 24, None)


def test_log_written_lists_an_older_amount(
    editor: Client, film: models.Project
) -> None:
    cue = _cue(film, "1m01", 10)
    # Logged before stretches: an amount, taken to run on from IN.
    models.WritingLog.objects.create(cue=cue, date=MON - _DAY, seconds=90)
    url = reverse("cues:log_written", args=[cue.pk])
    page = editor.get(url, headers=HX).content.decode()
    assert "1:30 written" in page
    assert 'value="1:01:30:00"' in page


def test_log_written_defaults_to_today(
    editor: Client, film: models.Project
) -> None:
    cue = _cue(film, "1m01", 10)
    url = reverse("cues:log_written", args=[cue.pk])
    form = editor.get(url, {"date": "x", "amount": "lots"}, headers=HX)
    assert b'value="2026-11-02"' in form.content
    assert b'value="1:10:00:00"' in form.content  # a bad share: to OUT
    assert editor.get(url)["Location"] == reverse("cues:cue", args=[cue.pk])


def test_the_day_list_shows_what_was_written(
    editor: Client, film: models.Project
) -> None:
    planned = _cue(film, "1m01", 2)
    extra = _cue(film, "1m02", 2)
    models.ScheduleEntry.objects.create(
        date=MON - _DAY, cue=planned, music=Decimal(120), effort=Decimal(120)
    )
    models.WritingLog.objects.create(cue=planned, date=MON - _DAY, seconds=60)
    models.WritingLog.objects.create(cue=extra, date=MON - _DAY, seconds=45)
    page = editor.get(
        reverse("cues:schedule"), {"day": "2026-11-01"}
    ).content.decode()
    assert "<strong>2:00</strong> of music planned" in page
    assert "<strong>1:45</strong> written" in page
    assert "Written 1:00" in page and "Written 0:45" in page
    assert "Log Written…" in page
    assert reverse("cues:log_written", args=[planned.pk]) in page
