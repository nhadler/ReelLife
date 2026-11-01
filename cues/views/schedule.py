"""The Schedule page: what to write each day."""

import calendar as month_calendar
from collections.abc import Callable, Collection, Iterable
import dataclasses
import datetime
from typing import Any

from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import dateformat
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_GET

from cues import dates
from cues import models
from cues.services import agenda as agenda_service
from cues.services import scheduler
from cues.services.summaries import clock

_DAY = datetime.timedelta(days=1)


@dataclasses.dataclass(frozen=True)
class PartView:
    """A cue, or its share, on a day."""

    cue: agenda_service.CueInfo
    project: agenda_service.ProjectInfo
    part: scheduler.Part

    @property
    def music(self) -> str:
        """This day's music, e.g. '1:20', or '1:20 of 4:00' for a share."""
        today = clock(round(self.part.music))
        if self.part.whole:
            return today
        return f"{today} of {clock(round(self.part.of_music))}"

    @property
    def share(self) -> str:
        """Just this day's music, for the calendar: '1:20'."""
        return clock(round(self.part.music))


@dataclasses.dataclass(frozen=True)
class DayView:
    """One day of the calendar."""

    date: datetime.date
    today: bool
    past: bool
    outside: bool
    off: list[str]
    parts: list[PartView]
    url: str = ""  # selects the day
    selected: bool = False

    @property
    def music(self) -> str:
        """The day's music, as m:ss, or '' with none."""
        total = sum(round(p.part.music) for p in self.parts)
        return clock(total) if total else ""


def _month(text: str | None, today: datetime.date) -> datetime.date:
    """The first of the month 'YYYY-MM' names, else this month."""
    try:
        year, month = (int(n) for n in (text or "").split("-"))
        return datetime.date(year, month, 1)
    except ValueError:
        return today.replace(day=1)


def _date(text: str | None, today: datetime.date) -> datetime.date:
    try:
        return datetime.date.fromisoformat(text or "")
    except ValueError:
        return today


def _week_start(date: datetime.date, sunday_first: bool) -> datetime.date:
    back = (date.weekday() + 1) % 7 if sunday_first else date.weekday()
    return date - datetime.timedelta(days=back)


def _visible(shown: Collection[int] | None, project: int) -> bool:
    """Whether a project's work is shown (None shows every project)."""
    return shown is None or project in shown


def days_view(
    agenda: agenda_service.Agenda,
    first: datetime.date,
    last: datetime.date,
    shown: Collection[int] | None,
    month: int | None = None,
    link: Callable[[datetime.date], str] | None = None,
    selected: datetime.date | None = None,
) -> list[DayView]:
    """Each day from `first` to `last`, with its cues and days off.

    Args:
        agenda: The plan.
        first: The first day shown.
        last: The last day shown.
        shown: Show only these projects' work, or None for all.
        month: The month shown; days of others are marked outside it.
        link: Each day's link (selecting it), if any.
        selected: The day picked for the day panel.

    Returns:
        The days, in order.
    """
    days = []
    date = first
    while date <= last:
        planned = agenda.plan.day(date)
        parts = [
            PartView(
                agenda.cues[part.key],
                agenda.projects[agenda.cues[part.key].project],
                part,
            )
            for part in (planned.parts if planned else ())
            if _visible(shown, agenda.cues[part.key].project)
        ]
        off = [
            note or "Day off"
            if owner is None
            else f"{agenda.projects[owner].title} off"
            + (f": {note}" if note else "")
            for owner, note in agenda.days_off.get(date, [])
            if owner is None
            or owner in agenda.projects
            and _visible(shown, owner)
        ]
        days.append(
            DayView(
                date=date,
                today=date == agenda.today,
                past=date < agenda.today,
                outside=month is not None and date.month != month,
                off=off,
                parts=parts,
                url=link(date) if link else "",
                selected=date == selected,
            )
        )
        date += _DAY
    return days


def summary(
    agenda: agenda_service.Agenda,
    shown: Collection[int] | None,
    formats: dict[str, str],
) -> tuple[str, list[str]]:
    """The one line about the plan, and its warnings (also the Overview's)."""
    plan = agenda.plan
    names = agenda.projects

    def short(date: datetime.date) -> str:
        return dateformat.format(date, formats["short_format"])

    risks = [
        f"At your daily maximum, {names[r.project].title} misses "
        f"{short(r.due)}: "
        + (
            f"it would finish {short(r.finish)}."
            if r.finish
            else "it wouldn't finish in the next three years."
        )
        for r in plan.risks
        if _visible(shown, r.project)
    ]
    parts = [
        (day, part)
        for day in plan.days
        for part in day.parts
        if _visible(shown, agenda.cues[part.key].project)
    ]
    if not parts:
        if any(
            _visible(shown, agenda.cues[key].project) for key in plan.unplaced
        ):
            return (
                "Nothing is planned: no project has a composition deadline "
                "ahead. Add one, or a daily maximum, to plan the work.",
                risks,
            )
        return "Nothing left to write.", risks
    music = sum(round(part.music) for _, part in parts)
    finish = max(day.date for day, _ in parts)
    working = len({day.date for day, _ in parts})
    cues = len({part.key for _, part in parts})
    noun = "cue" if cues == 1 else "cues"
    line = (
        f"{cues} {noun}, {clock(music)} of music, done by "
        f"{dateformat.format(finish, formats['day_format'])}: about "
        f"{clock(round(music / working))} a working day."
    )
    return line, risks


@dataclasses.dataclass(frozen=True)
class _Span:
    """The days shown and the links around them."""

    first: datetime.date
    last: datetime.date
    month: int | None
    title: str
    here: dict[str, str]
    home: datetime.date  # the week's or month's first day
    end: datetime.date  # and its last
    back: dict[str, str]
    ahead: dict[str, str]
    other: dict[str, str]


def _span(
    request: HttpRequest,
    today: datetime.date,
    settings: models.AppSettings,
) -> _Span:
    """The week or month the query asks for (else this one)."""
    sunday_first = settings.date_format == models.AppSettings.DateFormat.US
    if request.GET.get("view") == "week":
        first = _week_start(_date(request.GET.get("week"), today), sunday_first)
        return _Span(
            first=first,
            last=first + 6 * _DAY,
            month=None,
            title=dates.week(first, first + 6 * _DAY, sunday_first),
            here={"view": "week", "week": first.isoformat()},
            home=first,
            end=first + 6 * _DAY,
            back={"view": "week", "week": (first - 7 * _DAY).isoformat()},
            ahead={"view": "week", "week": (first + 7 * _DAY).isoformat()},
            other={"month": f"{first:%Y-%m}"},
        )
    month = _month(request.GET.get("month"), today)
    size = month_calendar.monthrange(month.year, month.month)[1]
    end = month.replace(day=size)
    start = today if month <= today <= end else month
    return _Span(
        first=_week_start(month, sunday_first),
        last=_week_start(end, sunday_first) + 6 * _DAY,
        month=month.month,
        title=dateformat.format(month, "F Y"),
        here={"month": f"{month:%Y-%m}"},
        home=month,
        end=end,
        back={"month": f"{month - _DAY:%Y-%m}"},
        ahead={"month": f"{end + _DAY:%Y-%m}"},
        other={"view": "week", "week": start.isoformat()},
    )


def _selected(
    request: HttpRequest, span: _Span, today: datetime.date
) -> datetime.date:
    """The day the panel shows: ?day= if shown, else today, else the first."""
    picked = _date(request.GET.get("day"), today)
    for day in (picked, today):
        if span.home <= day <= span.end:
            return day
    return span.home


def _shown(
    request: HttpRequest, agenda: agenda_service.Agenda
) -> frozenset[int] | None:
    """The projects ticked in the Projects list, or None for all.

    The list sends f=1 with the ticked projects' ids (p), so ticking none
    shows none; without f, every project shows.
    """
    if "f" not in request.GET:
        return None
    ids = {int(v) for v in request.GET.getlist("p") if v.isdigit()}
    return frozenset(ids & set(agenda.projects))


@require_GET
def schedule(request: HttpRequest) -> HttpResponse:
    """The month or week, with each day's cues and the chosen day's list."""
    today = timezone.localdate()
    agenda = agenda_service.build(today)
    settings = models.AppSettings.load()
    shown = _shown(request, agenda)
    span = _span(request, today, settings)
    keep = (
        [("f", "1"), *(("p", str(pk)) for pk in sorted(shown))]
        if shown is not None
        else []
    )

    def link(query: dict[str, str]) -> str:
        return "?" + urlencode([*query.items(), *keep])

    selected = _selected(request, span, today)
    here = {**span.here, "day": selected.isoformat()}
    days = days_view(
        agenda,
        span.first,
        span.last,
        shown,
        span.month,
        link=lambda date: link({**span.here, "day": date.isoformat()}),
        selected=selected,
    )
    line, risks = summary(agenda, shown, settings.date_formats())

    def listed(keys: Iterable[int]) -> list[tuple[Any, Any]]:
        return [
            (agenda.cues[key], agenda.projects[agenda.cues[key].project])
            for key in keys
            if _visible(shown, agenda.cues[key].project)
        ]

    return render(
        request,
        "cues/schedule.html",
        {
            "nav": "schedule",
            "week": span.month is None,
            "compact": span.month is not None,
            "title": span.title,
            "weekdays": [dateformat.format(d.date, "D") for d in days[:7]],
            "days": days,
            "day": next(d for d in days if d.selected),
            "projects": [
                (info, _visible(shown, info.id))
                for info in agenda.projects.values()
            ],
            "here": here.items(),
            "back_url": link(span.back),
            "ahead_url": link(span.ahead),
            "today_url": link({"view": "week"} if span.month is None else {}),
            "other_url": link(span.other),
            "line": line,
            "risks": risks,
            "unschedulable": listed(c.id for c in agenda.unschedulable),
            "unplaced": listed(agenda.plan.unplaced),
        },
    )
