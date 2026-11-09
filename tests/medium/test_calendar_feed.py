"""The schedule in a calendar app: feed links and the .ics feed."""

from collections.abc import Callable, Iterator
import datetime
from decimal import Decimal
import re

from django.test import Client
from django.urls import reverse
import freezegun
import icalendar
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import feed
from tests import factories

pytestmark = pytest.mark.django_db

MON = datetime.date(2026, 11, 2)
_DAY = datetime.timedelta(days=1)
MINUTE = 24 * 60


@pytest.fixture(autouse=True)
def fixture_today() -> Iterator[None]:
    with freezegun.freeze_time("2026-11-02 18:00:00"):
        yield


@pytest.fixture(name="film")
def fixture_film() -> models.Project:
    film = factories.ProjectFactory.create(title="Night Ferry", frame_rate="24")
    factories.DeadlineFactory.create(project=film, end_date=MON + 4 * _DAY)
    picture = factories.PictureFactory.create(project=film, name="1")
    status = film.statuses.filter(kind=models.StatusKind.TO_DO).first()
    for number, title, minutes in (("1m01", "Main Title", 3), ("1m02", "", 9)):
        factories.CueFactory.create(
            project=film,
            picture=picture,
            number=number,
            title=title,
            status=status,
            difficulty=models.Difficulty.HARD if minutes == 9 else "",
            tc_in=86_400,
            tc_out=86_400 + minutes * MINUTE,
        )
    return film


def _token(client: Client) -> str:
    page = client.post(
        reverse("cues:calendar_feed"), {"action": "create"}
    ).content.decode()
    found = re.search(r"/calendar/([A-Za-z0-9_-]+)\.ics", page)
    assert found is not None
    return found.group(1)


@pytest.mark.usefixtures("film")
def test_a_viewer_makes_reads_and_revokes_a_link(
    role_client: Callable[[Role], Client],
) -> None:
    viewer = role_client(Role.VIEWER)
    page = viewer.get(reverse("cues:calendar_feed")).content.decode()
    assert "No Calendar Link" in page
    token = _token(viewer)
    user = User.objects.get(role=Role.VIEWER)
    stored = models.CalendarFeed.objects.get(user=user)
    assert stored.token_hash != token and len(stored.token_hash) == 64
    # The link is shown once; afterwards only when it was made and read.
    again = viewer.get(reverse("cues:calendar_feed")).content.decode()
    assert token not in again and "Not yet" in again
    anonymous = Client()
    response = anonymous.get(f"/calendar/{token}.ics")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/calendar; charset=utf-8"
    stored.refresh_from_db()
    assert stored.last_used_at is not None
    # A new link replaces the old one.
    newer = _token(viewer)
    assert anonymous.get(f"/calendar/{token}.ics").status_code == 404
    assert anonymous.get(f"/calendar/{newer}.ics").status_code == 200
    viewer.post(reverse("cues:calendar_feed"), {"action": "revoke"})
    assert anonymous.get(f"/calendar/{newer}.ics").status_code == 404
    assert not models.CalendarFeed.objects.exists()


@pytest.mark.usefixtures("film")
def test_a_link_stops_working_without_access(
    role_client: Callable[[Role], Client],
) -> None:
    editor = role_client(Role.EDITOR)
    token = _token(editor)
    user = User.objects.get(role=Role.EDITOR)
    user.role = Role.NONE
    user.save()
    assert Client().get(f"/calendar/{token}.ics").status_code == 404
    user.role = Role.EDITOR
    user.is_active = False
    user.save()
    assert Client().get(f"/calendar/{token}.ics").status_code == 404
    assert Client().get("/calendar/nonsense.ics").status_code == 404


def test_the_feed_lists_each_days_cues(
    role_client: Callable[[Role], Client], film: models.Project
) -> None:
    cue = film.cues.get(number="1m01")
    models.ScheduleEntry.objects.create(
        date=MON - _DAY, cue=cue, music=Decimal(60), effort=Decimal(60)
    )
    body = role_client(Role.VIEWER).get(reverse("cues:calendar_download"))
    assert body["Content-Disposition"] == (
        'attachment; filename="reellife-schedule.ics"'
    )
    calendar = icalendar.Calendar.from_ical(body.content)
    events = list(calendar.walk("VEVENT"))
    assert min(e.decoded("dtstart") for e in events) == MON - _DAY  # as planned
    today = [e for e in events if e.decoded("dtstart") == MON]
    assert len(today) > 1  # one event per cue
    first = next(e for e in today if "1m01" in str(e["summary"]))
    assert str(first["summary"]).startswith("1m01 Main Title (")
    assert str(first["uid"]) == f"reellife-20261102-cue-{cue.pk}@testserver"
    assert str(first["url"]) == f"http://testserver/cues/{cue.pk}/"
    description = str(first["description"])
    assert description.startswith("Night Ferry · ")
    assert f"http://testserver/cues/{cue.pk}/" in description
    assert len({str(e["uid"]) for e in events}) == len(events)
    split = [e for e in events if " of " in str(e["summary"])]
    assert split  # the Hard cue is split: "(… of 9:00)"
    assert "Hard" in str(split[0]["description"])


def test_find_ignores_unknown_tokens() -> None:
    assert feed.find("x") is None


def test_the_feed_covers_30_days_back_and_90_ahead(
    role_client: Callable[[Role], Client],
) -> None:
    film = factories.ProjectFactory.create(title="Long Haul", frame_rate="24")
    factories.DeadlineFactory.create(project=film, end_date=MON + 200 * _DAY)
    picture = factories.PictureFactory.create(project=film, name="1")
    cue = factories.CueFactory.create(
        project=film,
        picture=picture,
        number="1m01",
        status=film.statuses.filter(kind=models.StatusKind.TO_DO).first(),
        tc_in=86_400,
        tc_out=86_400 + 400 * MINUTE,
    )
    for back in (10, 40):
        models.ScheduleEntry.objects.create(
            date=MON - back * _DAY,
            cue=cue,
            music=Decimal(60),
            effort=Decimal(60),
        )
    body = role_client(Role.VIEWER).get(reverse("cues:calendar_download"))
    dates = {
        e.decoded("dtstart")
        for e in icalendar.Calendar.from_ical(body.content).walk("VEVENT")
    }
    assert min(dates) == MON - 10 * _DAY
    assert max(dates) == MON + 90 * _DAY
