"""The Overview, the home page: this week and the active projects."""

import dataclasses
import datetime

from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import dateformat
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_GET

from cues import models
from cues.services import agenda as agenda_service
from cues.services import summaries
from cues.services.summaries import clock
from cues.views import schedule as schedule_view

# The days the Overview shows, from today.
DAYS = 7


@dataclasses.dataclass(frozen=True)
class ProjectRow:
    """An active project's compact row."""

    s: summaries.ProjectSummary
    week: str  # the music it has on the schedule in the coming days


def _soonest(row: ProjectRow) -> tuple[bool, datetime.date, str]:
    deadline = row.s.next_deadline
    return (
        deadline is None,
        deadline.date if deadline else datetime.date.max,
        row.s.project.title.lower(),
    )


@require_GET
def overview(request: HttpRequest) -> HttpResponse:
    """The next seven days of the schedule and every active project."""
    today = timezone.localdate()
    agenda = agenda_service.build(today)
    last = today + datetime.timedelta(days=DAYS - 1)
    schedule = reverse("cues:schedule")

    def link(date: datetime.date) -> str:
        week = {"view": "week", "week": date.isoformat()}
        return f"{schedule}?{urlencode({**week, 'day': date.isoformat()})}"

    days = schedule_view.days_view(agenda, today, last, None, link=link)
    week_music: dict[int, int] = {}
    for day in days:
        for view in day.parts:
            pk = view.cue.project
            week_music[pk] = week_music.get(pk, 0) + round(view.part.music)
    rows = sorted(
        (
            ProjectRow(
                summaries.for_project(project, today, with_map=True),
                clock(week_music[project.pk])
                if project.pk in week_music
                else "",
            )
            for project in models.Project.objects.filter(
                archived=False
            ).select_related("series")
        ),
        key=_soonest,
    )
    settings = models.AppSettings.load()
    line, risks = schedule_view.summary(agenda, None, settings.date_formats())
    return render(
        request,
        "cues/overview.html",
        {
            "nav": "overview",
            "weekdays": [dateformat.format(d.date, "D") for d in days],
            "days": days,
            "many_projects": len(agenda.projects) > 1,
            "line": line,
            "risks": risks,
            "rows": rows,
        },
    )
