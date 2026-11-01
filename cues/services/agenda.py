"""What the Schedule page shows: the plan and what's in it.

Gathers the facts `scheduler.plan` works from (every active project's
remaining cues, their deadlines, the days off and the settings), runs
it, and keeps what each cue and project needs to be shown.
"""

from collections import defaultdict
from collections.abc import Iterable
import dataclasses
import datetime
from decimal import Decimal
from typing import Any

from django.db.models import Q
from django.urls import reverse

from cues import models
from cues import timecode
from cues.services import scheduler
from cues.services import summaries

_PRIORITY = {
    models.Priority.HIGH: 0,
    models.Priority.MEDIUM: 1,
    models.Priority.LOW: 2,
}


@dataclasses.dataclass(frozen=True)
class CueInfo:
    """A cue as the schedule shows it."""

    id: int
    project: int
    number: str
    title: str
    color: str
    url: str
    difficulty: str
    revise: bool
    status: str = ""


# How many project colours the calendar has (app.css --tone-0 to -7).
TONES = 8


@dataclasses.dataclass(frozen=True)
class ProjectInfo:
    """A project with work on the schedule."""

    id: int
    title: str
    production_number: str
    due: datetime.date | None

    @property
    def tone(self) -> int:
        """Its colour on the calendar, one of eight (stable per project)."""
        return self.id % TONES


# Each day off's notes, with the project for a project's own (else None).
DaysOff = dict[datetime.date, list[tuple[int | None, str]]]


@dataclasses.dataclass(frozen=True)
class Agenda:
    """The plan from today, and what it needs to be shown.

    Attributes:
        today: The plan's first day.
        plan: The work dealt out over the days.
        cues: Every cue in the plan or unschedulable, by id.
        projects: Every active project with work, by id.
        unschedulable: Cues to write that have no IN or OUT, so no
            length to plan.
        days_off: Each day off from today: its notes, and the project
            for a project's own (None for the composer's).
    """

    today: datetime.date
    plan: scheduler.Plan
    cues: dict[int, CueInfo]
    projects: dict[int, ProjectInfo]
    unschedulable: list[CueInfo]
    days_off: DaysOff


def _factors(
    settings: models.AppSettings,
) -> tuple[dict[str, Decimal], Decimal]:
    by_difficulty: dict[str, Decimal] = {
        models.Difficulty.EASY: settings.schedule_easy,
        models.Difficulty.MODERATE: settings.schedule_moderate,
        models.Difficulty.HARD: settings.schedule_hard,
    }
    by_difficulty[""] = by_difficulty[settings.schedule_unrated]
    return by_difficulty, settings.schedule_revise


def _projects(
    today: datetime.date,
) -> tuple[dict[int, models.Project], dict[int, ProjectInfo]]:
    """Active projects, and each one's pace deadline."""
    projects = {p.pk: p for p in models.Project.objects.filter(archived=False)}
    infos = {}
    for pk, project in projects.items():
        deadline = summaries.pace_deadline(project, today)
        infos[pk] = ProjectInfo(
            id=pk,
            title=project.title,
            production_number=project.production_number,
            due=deadline.end_date if deadline else None,
        )
    return projects, infos


_FIELDS = (
    "id",
    "project_id",
    "number",
    "title",
    "tc_in",
    "tc_out",
    "status__kind",
    "status__color",
    "status__label",
    "priority",
    "difficulty",
    "picture__order",
)


def _work(
    row: dict[str, Any],
    project: models.Project,
    due: datetime.date | None,
    settings: models.AppSettings,
) -> scheduler.Work:
    """A cue row with an IN and OUT as the scheduler's work."""
    by_difficulty, revise_factor = _factors(settings)
    factor = scheduler.Fraction(by_difficulty[row["difficulty"]])
    kind = row["status__kind"] or models.StatusKind.TO_DO
    if kind in summaries.REVISE_KINDS:
        factor *= scheduler.Fraction(revise_factor)
    return scheduler.Work(
        key=row["id"],
        project=project.pk,
        music=timecode.seconds(row["tc_out"] - row["tc_in"], project.rate),
        factor=factor,
        due=due,
        rank=(
            _PRIORITY.get(row["priority"], 3),
            project.title,
            row["picture__order"],
            row["tc_in"],
            row["number"],
        ),
        started=kind == models.StatusKind.IN_PROGRESS,
    )


def _calendar(
    today: datetime.date, projects: Iterable[int]
) -> tuple[scheduler.Calendar, DaysOff]:
    """The days off from today, as the scheduler and the page want them."""
    notes: DaysOff = defaultdict(list)
    shared: set[datetime.date] = set()
    own: dict[int, set[datetime.date]] = defaultdict(set)
    for day_off in models.DayOff.objects.filter(date__gte=today).filter(
        Q(project__isnull=True) | Q(project__in=list(projects))
    ):
        notes[day_off.date].append((day_off.project_id, day_off.note))
        if day_off.project_id is None:
            shared.add(day_off.date)
        else:
            own[day_off.project_id].add(day_off.date)
    calendar = scheduler.Calendar(
        today=today,
        shared_off=frozenset(shared),
        project_off={pk: frozenset(dates) for pk, dates in own.items()},
    )
    return calendar, dict(notes)


def build(today: datetime.date) -> Agenda:
    """Plans the remaining work of every active project from `today`."""
    settings = models.AppSettings.load()
    projects, infos = _projects(today)
    kinds = summaries.WRITE_KINDS | summaries.REVISE_KINDS
    rows = (
        models.Cue.objects.filter(active=True, project__in=list(projects))
        .filter(Q(status__isnull=True) | Q(status__kind__in=kinds))
        .values(*_FIELDS)
    )
    works: list[scheduler.Work] = []
    cues: dict[int, CueInfo] = {}
    unschedulable: list[CueInfo] = []
    for row in rows:
        info = CueInfo(
            id=row["id"],
            project=row["project_id"],
            number=row["number"],
            title=row["title"],
            color=row["status__color"] or "",
            url=reverse("cues:cue", args=[row["id"]]),
            difficulty=row["difficulty"],
            revise=row["status__kind"] in summaries.REVISE_KINDS,
            status=row["status__label"] or "",
        )
        cues[info.id] = info
        tc_in, tc_out = row["tc_in"], row["tc_out"]
        if tc_in is None or tc_out is None or tc_out <= tc_in:
            unschedulable.append(info)
        else:
            works.append(
                _work(
                    row,
                    projects[info.project],
                    infos[info.project].due,
                    settings,
                )
            )
    calendar, days_off = _calendar(today, projects)
    plan = scheduler.plan(
        works,
        calendar,
        daily_max=settings.schedule_daily_max,
        min_part=settings.schedule_min_part,
        parallel=(
            settings.schedule_projects
            == models.AppSettings.ProjectsAtOnce.PARALLEL
        ),
    )
    return Agenda(
        today=today,
        plan=plan,
        cues=cues,
        projects={
            pk: infos[pk] for pk in sorted({c.project for c in cues.values()})
        },
        unschedulable=sorted(
            unschedulable, key=lambda c: (c.project, c.number)
        ),
        days_off=days_off,
    )
