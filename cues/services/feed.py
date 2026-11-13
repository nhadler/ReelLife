"""The schedule as a calendar subscription (an .ics feed).

Each user can have one feed link. Its token is 32 random bytes, shown
once when it's made; only its SHA-256 is kept, and it's looked up by
that. Making a new link revokes the old. A feed stops working once its
user is inactive or loses every ReelTime role.

The feed has an all-day event for each cue on each day it's planned:
the last 30 days as they were planned, and the next 90.
"""

from collections.abc import Iterable, Iterator
import datetime
import hashlib
import secrets

from django.utils import timezone
import icalendar

from accounts import roles
from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import agenda as agenda_service
from cues.services import scheduler
from cues.services.summaries import clock

PAST_DAYS = 30
AHEAD_DAYS = 90


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(user: User) -> str:
    """Makes the user's feed link, revoking any older one.

    Returns:
        The token, which is shown once and never stored.
    """
    token = secrets.token_urlsafe(32)
    models.CalendarFeed.objects.filter(user=user).delete()
    models.CalendarFeed.objects.create(user=user, token_hash=_hash(token))
    return token


def revoke(user: User) -> None:
    """Stops the user's feed link working."""
    models.CalendarFeed.objects.filter(user=user).delete()


def find(token: str) -> models.CalendarFeed | None:
    """The feed a token opens, if its user may still see the schedule."""
    feed = (
        models.CalendarFeed.objects.select_related("user")
        .filter(token_hash=_hash(token))
        .first()
    )
    if feed is None:
        return None
    user = feed.user
    if not user.is_active or not roles.at_least(user.role, Role.VIEWER):
        return None
    return feed


def _music(part: scheduler.Part) -> str:
    """'1:20', or '1:20 of 4:00' for a share of a longer cue."""
    today = clock(round(part.music))
    return today if part.whole else f"{today} of {clock(round(part.of_music))}"


def _events(
    agenda: agenda_service.Agenda,
    date: datetime.date,
    parts: Iterable[scheduler.Part],
    base_url: str,
    host: str,
) -> Iterator[icalendar.Event]:
    """A day's cues, each as its own all-day event."""
    for part in parts:
        cue = agenda.cues[part.key]
        project = agenda.projects.get(cue.project)
        name = " ".join(filter(None, (cue.number, cue.title)))
        facts = [
            project.title if project else "",
            _music(part),
            cue.difficulty,
            "Revision" if cue.revise else "",
        ]
        event = icalendar.Event()
        event.add("uid", f"reeltime-{date:%Y%m%d}-cue-{cue.id}@{host}")
        event.add("dtstamp", timezone.now())
        event.add("dtstart", date)
        event.add("dtend", date + datetime.timedelta(days=1))
        event.add("summary", f"{name} ({_music(part)})")
        event.add(
            "description",
            f"{' · '.join(filter(None, facts))}\n{base_url}{cue.url}",
        )
        event.add("url", f"{base_url}{cue.url}")
        event.add("transp", "TRANSPARENT")
        yield event


def ics(agenda: agenda_service.Agenda, base_url: str) -> bytes:
    """The schedule around `agenda.today` as an iCalendar file."""
    host = base_url.split("://", 1)[-1].split("/", 1)[0] or "reeltime"
    calendar = icalendar.Calendar()
    calendar.add("prodid", "-//ReelTime//Schedule//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", "ReelTime Schedule")
    calendar.add("x-published-ttl", "PT1H")
    first = agenda.today - datetime.timedelta(days=PAST_DAYS)
    for date in sorted(agenda.past):
        if date >= first:
            parts = [
                scheduler.Part(p.key, p.effort, p.music, p.music)
                for p in agenda.past[date]
            ]
            for event in _events(agenda, date, parts, base_url, host):
                calendar.add_component(event)
    last = agenda.today + datetime.timedelta(days=AHEAD_DAYS)
    for day in agenda.plan.days:
        if day.date <= last:
            for event in _events(agenda, day.date, day.parts, base_url, host):
                calendar.add_component(event)
    return bytes(calendar.to_ical())
