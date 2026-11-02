"""The schedule as a calendar subscription (an .ics feed).

Each user can have one feed link. Its token is 32 random bytes, shown
once when it's made; only its SHA-256 is kept, and it's looked up by
that. Making a new link revokes the old. A feed stops working once its
user is inactive or loses every ReelLife role.

The feed has one all-day event per day with work: the last 30 days as
they were planned, and the next 90.
"""

from collections.abc import Iterable
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


def _event(
    agenda: agenda_service.Agenda,
    date: datetime.date,
    parts: Iterable[scheduler.Part],
    base_url: str,
    host: str,
) -> icalendar.Event:
    """A day's event: its cues in the title, each in the description."""
    shown = list(parts)
    total = clock(sum(round(p.music) for p in shown))
    names = ", ".join(
        " ".join(
            filter(None, (agenda.cues[p.key].number, agenda.cues[p.key].title))
        )
        for p in shown
    )
    lines = []
    for part in shown:
        cue = agenda.cues[part.key]
        project = agenda.projects.get(cue.project)
        facts = [
            " ".join(filter(None, (cue.number, cue.title))),
            project.title if project else "",
            _music(part),
            cue.difficulty,
            "Revision" if cue.revise else "",
        ]
        lines.append(" · ".join(filter(None, facts)))
        lines.append(f"{base_url}{cue.url}")
    event = icalendar.Event()
    event.add("uid", f"reellife-schedule-{date:%Y%m%d}@{host}")
    event.add("dtstamp", timezone.now())
    event.add("dtstart", date)
    event.add("dtend", date + datetime.timedelta(days=1))
    event.add("summary", f"ReelLife: {names} ({total})")
    event.add("description", "\n".join(lines))
    event.add("url", f"{base_url}/schedule/?day={date.isoformat()}")
    event.add("transp", "TRANSPARENT")
    return event


def ics(agenda: agenda_service.Agenda, base_url: str) -> bytes:
    """The schedule around `agenda.today` as an iCalendar file."""
    host = base_url.split("://", 1)[-1].split("/", 1)[0] or "reellife"
    calendar = icalendar.Calendar()
    calendar.add("prodid", "-//ReelLife//Schedule//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", "ReelLife Schedule")
    calendar.add("x-published-ttl", "PT1H")
    first = agenda.today - datetime.timedelta(days=PAST_DAYS)
    for date in sorted(agenda.past):
        if date >= first:
            parts = [
                scheduler.Part(p.key, p.effort, p.music, p.music)
                for p in agenda.past[date]
            ]
            calendar.add_component(_event(agenda, date, parts, base_url, host))
    last = agenda.today + datetime.timedelta(days=AHEAD_DAYS)
    for day in agenda.plan.days:
        if day.date <= last:
            calendar.add_component(
                _event(agenda, day.date, day.parts, base_url, host)
            )
    return bytes(calendar.to_ical())
