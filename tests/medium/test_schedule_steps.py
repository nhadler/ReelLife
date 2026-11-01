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
    assert "Schedule 1m01" in form and 'type="date"' in form
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


def test_a_cue_in_progress_is_credited_with_past_days(
    film: models.Project,
) -> None:
    cue = _cue(film, "1m01", 20, models.StatusKind.IN_PROGRESS)
    for offset, minutes in ((2, 6), (1, 3)):
        models.ScheduleEntry.objects.create(
            date=MON - offset * _DAY,
            cue=cue,
            music=Decimal(minutes * 60),
            effort=Decimal(minutes * 60),
        )
    found = agenda.build(MON, save=False)
    parts = [p for d in found.plan.days for p in d.parts if p.key == cue.pk]
    # 20 minutes, 9 planned on past days: 11 left.
    assert sum(p.music for p in parts) == 11 * 60
    # Past days show what they planned.
    assert [p.key for p in found.past[MON - _DAY]] == [cue.pk]


def test_credit_always_leaves_a_minimum_part(film: models.Project) -> None:
    cue = _cue(film, "1m01", 5, models.StatusKind.IN_PROGRESS)
    models.ScheduleEntry.objects.create(
        date=MON - _DAY, cue=cue, music=Decimal(600), effort=Decimal(600)
    )
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
    assert str(pin) == f"{cue} on 2026-11-02"
    assert str(entry) == f"2026-11-02 cue {cue.pk}"
    assert str(feed).startswith("Calendar feed for ")
