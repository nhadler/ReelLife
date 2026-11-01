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
class PastPart:
    """A cue's share of a past day, as planned that day."""

    key: int
    music: scheduler.Fraction
    effort: scheduler.Fraction


@dataclasses.dataclass(frozen=True)
class Agenda:
    """The plan from today, and what it needs to be shown.

    Attributes:
        today: The plan's first day.
        plan: The work dealt out over the days.
        cues: Every cue in the plan, unschedulable or planned on a past
            day, by id.
        projects: Every active project with work, by id.
        unschedulable: Cues to write that have no IN or OUT, so no
            length to plan.
        days_off: Each day off from today: its notes, and the project
            for a project's own (None for the composer's).
        past: What each past day planned (the saved plans), recent
            days only (PAST_DAYS).
        written: Seconds logged as written each recent day, by cue, to
            today.
    """

    today: datetime.date
    plan: scheduler.Plan
    cues: dict[int, CueInfo]
    projects: dict[int, ProjectInfo]
    unschedulable: list[CueInfo]
    days_off: DaysOff
    past: dict[datetime.date, list[PastPart]] = dataclasses.field(
        default_factory=dict
    )
    written: dict[datetime.date, dict[int, int]] = dataclasses.field(
        default_factory=dict
    )


# How far back the calendar (and the feed) show what was planned.
PAST_DAYS = 62
# Saved plans older than this are pruned (by the nightly job).
KEEP_ENTRIES_DAYS = 366


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


@dataclasses.dataclass(frozen=True)
class _Facts:
    """What a cue's work depends on beyond its row."""

    due: datetime.date | None
    pinned: datetime.date | None = None
    # Seconds of music logged as written, for its current kind of work.
    written: int = 0


def _info(row: dict[str, Any]) -> CueInfo:
    """A cue row (_FIELDS) as the schedule shows it."""
    return CueInfo(
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


def _work(
    row: dict[str, Any],
    project: models.Project,
    facts: _Facts,
    settings: models.AppSettings,
) -> scheduler.Work:
    """A cue row with an IN and OUT as the scheduler's work.

    What's logged as written comes off, leaving at least a minimum part
    until the cue is marked composed.
    """
    by_difficulty, revise_factor = _factors(settings)
    factor = scheduler.Fraction(by_difficulty[row["difficulty"]])
    kind = row["status__kind"] or models.StatusKind.TO_DO
    if kind in summaries.REVISE_KINDS:
        factor *= scheduler.Fraction(revise_factor)
    music = timecode.seconds(row["tc_out"] - row["tc_in"], project.rate)
    if facts.written:
        effort = music * factor
        least = min(scheduler.Fraction(settings.schedule_min_part), effort)
        music = max(effort - facts.written * factor, least) / factor
    return scheduler.Work(
        key=row["id"],
        project=project.pk,
        music=music,
        factor=factor,
        due=facts.due,
        pinned=facts.pinned,
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


def _written(rows: Iterable[dict[str, Any]]) -> dict[int, int]:
    """Seconds logged for each cue, for the kind of work it has now.

    Writing logs don't count toward revisions, nor the other way round.
    """
    revising = {
        row["id"]: row["status__kind"] in summaries.REVISE_KINDS for row in rows
    }
    written: dict[int, int] = defaultdict(int)
    for cue, revise, seconds in models.WritingLog.objects.filter(
        cue__in=list(revising)
    ).values_list("cue", "revise", "seconds"):
        if revise == revising[cue]:
            written[cue] += seconds
    return written


def _facts(
    rows: list[dict[str, Any]],
    infos: dict[int, ProjectInfo],
) -> dict[int, _Facts]:
    """Each cue's due date, pin and what's logged as written."""
    pins = dict(
        models.SchedulePin.objects.filter(
            cue__in=[row["id"] for row in rows]
        ).values_list("cue", "date")
    )
    written = _written(rows)
    return {
        row["id"]: _Facts(
            due=infos[row["project_id"]].due,
            pinned=pins.get(row["id"]),
            written=written.get(row["id"], 0),
        )
        for row in rows
    }


def _past(
    today: datetime.date,
) -> tuple[dict[datetime.date, list[PastPart]], set[int]]:
    """The saved plans of recent past days, and the cues in them."""
    past: dict[datetime.date, list[PastPart]] = defaultdict(list)
    for entry in models.ScheduleEntry.objects.filter(
        date__lt=today, date__gte=today - datetime.timedelta(days=PAST_DAYS)
    ).order_by("date", "id"):
        past[entry.date].append(
            PastPart(
                entry.cue_id,
                scheduler.Fraction(entry.music),
                scheduler.Fraction(entry.effort),
            )
        )
    return dict(past), {part.key for parts in past.values() for part in parts}


def _decimal(value: scheduler.Fraction) -> Decimal:
    """Seconds to three places, as ScheduleEntry stores them."""
    return round(Decimal(value.numerator) / value.denominator, 3)


def _logged(
    today: datetime.date,
) -> dict[datetime.date, dict[int, int]]:
    """What was logged as written each recent day, by cue."""
    logged: dict[datetime.date, dict[int, int]] = defaultdict(dict)
    for date, cue, seconds in models.WritingLog.objects.filter(
        date__lte=today, date__gte=today - datetime.timedelta(days=PAST_DAYS)
    ).values_list("date", "cue", "seconds"):
        logged[date][cue] = seconds
    return dict(logged)


def record(agenda: Agenda) -> None:
    """Saves today's plan, once a day (the first look, or the nightly job).

    Later looks the same day keep the first: what was planned for the
    day is what counts as written on it.
    """
    today = agenda.plan.day(agenda.today)
    if (
        today is None
        or models.ScheduleEntry.objects.filter(date=agenda.today).exists()
    ):
        return
    models.ScheduleEntry.objects.bulk_create(
        [
            models.ScheduleEntry(
                date=agenda.today,
                cue_id=part.key,
                revise=agenda.cues[part.key].revise,
                music=_decimal(part.music),
                effort=_decimal(part.effort),
            )
            for part in today.parts
        ],
        ignore_conflicts=True,
    )


def build(today: datetime.date, *, save: bool = True) -> Agenda:
    """Plans the remaining work of every active project from `today`.

    With `save`, today's plan is saved the first time it's built (see
    `record`).
    """
    settings = models.AppSettings.load()
    projects, infos = _projects(today)
    rows = list(
        models.Cue.objects.filter(active=True, project__in=list(projects))
        .filter(
            Q(status__isnull=True)
            | Q(status__kind__in=summaries.WRITE_KINDS | summaries.REVISE_KINDS)
        )
        .values(*_FIELDS)
    )
    facts = _facts(rows, infos)
    works: list[scheduler.Work] = []
    cues: dict[int, CueInfo] = {}
    unschedulable: list[CueInfo] = []
    for row in rows:
        info = _info(row)
        cues[info.id] = info
        if (
            row["tc_in"] is None
            or row["tc_out"] is None
            or row["tc_out"] <= row["tc_in"]
        ):
            unschedulable.append(info)
        else:
            works.append(
                _work(row, projects[info.project], facts[info.id], settings)
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
    past, past_cues = _past(today)
    logged = _logged(today)
    past_cues |= {cue for day in logged.values() for cue in day}
    _add_past_cues(cues, past_cues - set(cues))
    agenda = Agenda(
        today=today,
        plan=plan,
        cues=cues,
        projects={
            pk: infos[pk]
            for pk in sorted({c.project for c in cues.values()})
            if pk in infos
        },
        unschedulable=sorted(
            unschedulable, key=lambda c: (c.project, c.number)
        ),
        days_off=days_off,
        past={
            day: [part for part in parts if part.key in cues]
            for day, parts in past.items()
        },
        written={
            day: {cue: n for cue, n in by_cue.items() if cue in cues}
            for day, by_cue in logged.items()
        },
    )
    if save:
        record(agenda)
    return agenda


def _add_past_cues(cues: dict[int, CueInfo], ids: Iterable[int]) -> None:
    """Adds the cues only past days planned (finished since, say)."""
    for row in models.Cue.objects.filter(
        pk__in=list(ids), project__archived=False
    ).values(*_FIELDS):
        cues[row["id"]] = _info(row)


def nightly(today: datetime.date) -> int:
    """Saves today's plan if nobody has looked yet, and prunes old ones.

    Returns:
        How many saved entries were pruned.
    """
    build(today)
    cutoff = today - datetime.timedelta(days=KEEP_ENTRIES_DAYS)
    pruned, _ = models.ScheduleEntry.objects.filter(date__lt=cutoff).delete()
    return pruned
