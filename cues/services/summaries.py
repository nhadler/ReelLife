"""What the dashboard and project header show about a project."""

from collections import defaultdict
import dataclasses
import datetime
import fractions
import math

from django.db.models import Q
from django.utils import timezone

from cues import models
from cues import timecode
from cues.services import cuemap
from cues.services import progress

# Deadlines this close are highlighted.
SOON_DAYS = 14
# Still to compose vs. to revise, for the daily pace.
WRITE_KINDS = frozenset(
    {models.StatusKind.TO_DO, models.StatusKind.IN_PROGRESS}
)
REVISE_KINDS = frozenset({models.StatusKind.REDO, models.StatusKind.CHANGES})


@dataclasses.dataclass(frozen=True)
class NextDeadline:
    """The nearest deadline that hasn't passed."""

    label: str
    date: datetime.date
    days_left: int

    @property
    def soon(self) -> bool:
        """Within SOON_DAYS."""
        return self.days_left <= SOON_DAYS


def clock(seconds: int) -> str:
    """Seconds as m:ss, or h:mm:ss from an hour."""
    minutes, secs = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    return (
        f"{hours}:{minutes:02d}:{secs:02d}"
        if hours
        else f"{minutes}:{secs:02d}"
    )


@dataclasses.dataclass(frozen=True)
class Pace:
    """How much music a day it takes to make the composition deadline.

    Attributes:
        due: The composition deadline.
        days: Days left to work: today, or the deadline's start date
            while it's ahead, through the deadline, every day but days
            off (decided Oct 30; start dates Nov 1).
        calendar_days: The same span counting every day.
        write_seconds: Music still to compose (To Write, In Progress),
            per working day, rounded up.
        revise_seconds: Music to revise (Rewrite, Revisions), per
            working day, rounded up.
    """

    due: datetime.date
    days: int
    calendar_days: int
    write_seconds: int
    revise_seconds: int

    @property
    def write(self) -> str:
        """Per day, as m:ss."""
        return clock(self.write_seconds)

    @property
    def revise(self) -> str:
        """Per day, as m:ss."""
        return clock(self.revise_seconds)

    @property
    def total(self) -> str:
        """Writing and revising per day, as m:ss."""
        return clock(self.write_seconds + self.revise_seconds)


def _per_day(total: fractions.Fraction, days: int) -> int:
    """Whole seconds a day, rounded up; everything at once with no days."""
    return math.ceil(total / days) if days else math.ceil(total)


def pace_deadline(
    project: models.Project, today: datetime.date
) -> models.Deadline | None:
    """The deadline the daily pace counts down to.

    The one chosen in the stats panel while it is still ahead, else the
    next composition deadline.
    """
    chosen = project.pace_deadline
    if chosen is not None and chosen.end_date >= today:
        return chosen
    return (
        project.deadlines.filter(
            kind=models.Deadline.Kind.COMPOSITION, end_date__gte=today
        )
        .order_by("end_date")
        .first()
    )


def pace(project: models.Project, today: datetime.date) -> Pace | None:
    """The daily pace to the pace deadline, if there is one.

    Only active cues count, like the progress totals; cues without both
    IN and OUT add nothing.
    """
    deadline = pace_deadline(project, today)
    if deadline is None:
        return None
    end = deadline.end_date
    # Work waits for the start date (the database keeps it by the end).
    first = max(today, deadline.start_date or today)
    # Shared days off and this project's own, each date once.
    off = (
        models.DayOff.objects.filter(date__range=(first, end))
        .filter(Q(project__isnull=True) | Q(project=project))
        .values("date")
        .distinct()
        .count()
    )
    calendar_days = (end - first).days + 1
    days = calendar_days - off
    write = revise = fractions.Fraction(0)
    rate = project.rate
    rows = project.cues.filter(active=True).values_list(
        "tc_in", "tc_out", "status__kind"
    )
    for tc_in, tc_out, kind in rows:
        if tc_in is None or tc_out is None:
            continue
        seconds = timecode.seconds(max(tc_out - tc_in, 0), rate)
        kind = kind or models.StatusKind.TO_DO
        if kind in WRITE_KINDS:
            write += seconds
        elif kind in REVISE_KINDS:
            revise += seconds
    return Pace(
        due=end,
        days=days,
        calendar_days=calendar_days,
        write_seconds=_per_day(write, days),
        revise_seconds=_per_day(revise, days),
    )


@dataclasses.dataclass(frozen=True)
class Breakdown:
    """The spreadsheet-style breakdown (Nick, Nov 1), as display text.

    Attributes:
        total: All the music counted, e.g. '9:39'.
        cues: How many cues that is.
        by_type: (Score/Source/Song, duration) for active cues, non-zero.
        licensed: Music to license (status Licensed), or '' if none.
        parts: (label, css, duration) per status group, as the progress
            bar shows them.
        remaining: Music not composed yet.
    """

    total: str
    cues: int
    by_type: list[tuple[str, str]]
    licensed: str
    parts: list[tuple[str, str, str]]
    remaining: str


def breakdown(project: models.Project, result: progress.Progress) -> Breakdown:
    """The project's breakdown, from its progress plus one query.

    Score, Source and Song split the same music the total counts, so they
    add up to it; To License is all active licensed music, counted in
    the total or not.
    """
    rate = project.rate
    fmt = timecode.format_duration
    include = progress.includes_songs_and_licensed()
    by_type: dict[str, int] = defaultdict(int)
    licensed = 0
    for tc_in, tc_out, cue_type, kind in project.cues.filter(
        active=True, tc_in__isnull=False, tc_out__isnull=False
    ).values_list("tc_in", "tc_out", "cue_type", "status__kind"):
        frames = max((tc_out or 0) - (tc_in or 0), 0)
        kind = kind or models.StatusKind.TO_DO
        if progress.counts(kind, True, include_songs_and_licensed=include):
            by_type[cue_type] += frames
        if kind == models.StatusKind.LICENSE:
            licensed += frames
    return Breakdown(
        total=fmt(result.total.frames, rate),
        cues=result.total.cues,
        by_type=[
            (str(label), fmt(by_type[code], rate))
            for code, label in models.Cue.CueType.choices
            if by_type[code]
        ],
        licensed=fmt(licensed, rate) if licensed else "",
        parts=[
            (segment.label, segment.css, fmt(segment.frames, rate))
            for segment in result.segments()
        ],
        remaining=fmt(result.remaining.frames, rate),
    )


@dataclasses.dataclass(frozen=True)
class ProjectSummary:  # pylint: disable=too-many-instance-attributes
    """A project's headline numbers (a plain record the pages read)."""

    project: models.Project
    composed: str
    remaining: str
    percent: int
    has_cues: bool
    cue_map: cuemap.CueMap | None  # only the dashboard's cards draw it
    needs_conform: int
    next_deadline: NextDeadline | None
    pace: Pace | None = None
    # Composed cues, composed durations and remaining, without labels.
    figures: tuple[str, str, str] = ("", "", "")
    cues_done: int = 0
    cues_total: int = 0
    segments: list[progress.Segment] = dataclasses.field(default_factory=list)
    breakdown: Breakdown | None = None


def next_deadline(
    project: models.Project, today: datetime.date
) -> NextDeadline | None:
    """The nearest deadline on or after `today`."""
    deadline = (
        project.deadlines.filter(end_date__gte=today)
        .order_by("end_date")
        .first()
    )
    if deadline is None:
        return None
    return NextDeadline(
        str(deadline), deadline.end_date, (deadline.end_date - today).days
    )


def needs_conform_count(project: models.Project) -> int:
    """How many active cues' current versions are on older picture."""
    current = progress.current_picture_versions(project)
    rows = project.cues.filter(
        active=True, current_version__written_to__isnull=False
    ).values_list("picture_id", "current_version__written_to_id")
    return sum(
        1
        for picture_id, written_to in rows
        if picture_id in current and current[picture_id] != written_to
    )


def for_project(
    project: models.Project,
    today: datetime.date | None = None,
    *,
    with_map: bool = False,
) -> ProjectSummary:
    """Collects the summary (a handful of queries).

    Args:
        project: The project.
        today: For deadlines and pace; defaults to today.
        with_map: Also build the cue map (a rectangle per cue), which only
            the dashboard shows.
    """
    result = progress.for_project(project)
    composed, remaining = result.summary(project.rate)
    today = today or timezone.localdate()
    return ProjectSummary(
        project=project,
        composed=composed,
        remaining=remaining,
        percent=result.percent,
        has_cues=project.cues.exists(),
        cue_map=cuemap.for_project(project) if with_map else None,
        needs_conform=needs_conform_count(project),
        next_deadline=next_deadline(project, today),
        pace=pace(project, today),
        figures=result.details(project.rate),
        cues_done=result.composed.cues,
        cues_total=result.total.cues,
        segments=result.segments(),
        breakdown=breakdown(project, result),
    )


def total_pace(summaries: list[ProjectSummary]) -> tuple[str, str] | None:
    """Write and revise per day across projects ("How we doin'?")."""
    paces = [
        s.pace
        for s in summaries
        if s.pace is not None
        and (s.pace.write_seconds or s.pace.revise_seconds)
    ]
    if len(paces) < 2:
        return None
    return (
        clock(sum(p.write_seconds for p in paces)),
        clock(sum(p.revise_seconds for p in paces)),
    )
