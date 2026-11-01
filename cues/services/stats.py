"""The stats panel's figures.

Counts and durations of a project's active cues, grouped the ways the
panel shows them. Each row also carries the grid filter it stands for
(a filter group and its values), so the panel can select or filter to
those cues.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
import dataclasses
import datetime

from django.utils import timezone

from cues import models
from cues import timecode
from cues.services import progress
from cues.services import projects as project_service
from cues.services import summaries


@dataclasses.dataclass(frozen=True)
class Row:
    """One line of a stats section.

    Attributes:
        label: What the cues have in common.
        cues: How many.
        duration: Their summed length, e.g. '12:40'.
        percent: Their share of the section's duration, rounded.
        color: A swatch colour, or ''.
        css: A named swatch colour (stats__swatch--<css>), or ''.
        group: The grid filter group the row stands for ('status' …).
        values: That group's values for these cues, as the grid has them.
    """

    label: str
    cues: int
    duration: str
    percent: int
    color: str = ""
    css: str = ""
    group: str = ""
    values: tuple[str, ...] = ()

    @property
    def filter_values(self) -> str:
        """The values comma-separated, for a data attribute."""
        return ",".join(self.values)


@dataclasses.dataclass(frozen=True)
class DeadlineRow:
    """A deadline in the panel's Deadlines list."""

    pk: int
    label: str
    date: datetime.date
    days_left: int
    chosen: bool

    @property
    def passed(self) -> bool:
        """Before today: it can't drive the pace."""
        return self.days_left < 0


@dataclasses.dataclass(frozen=True)
class Stats:
    """Everything the panel shows."""

    totals: list[Row]
    progress: list[Row]
    pace: summaries.Pace | None
    pace_label: str
    deadlines: list[DeadlineRow]
    status: list[Row]
    cue_type: list[Row]
    sub_status: list[Row]
    themes: list[Row]
    instrumentation: list[Row]
    details: list[tuple[str, str]]  # Project details: (name, value)


@dataclasses.dataclass(frozen=True)
class _Cue:
    """What the panel needs about one active cue."""

    frames: int
    status_id: int | None
    kind: str
    cue_type: str
    sub_status_id: int | None
    # (option id, seconds it plays or None for the whole cue)
    themes: tuple[tuple[int, int | None], ...]
    instruments: tuple[tuple[int, int | None], ...]

    @property
    def theme_ids(self) -> tuple[int, ...]:
        """The current version's themes."""
        return tuple(pk for pk, _ in self.themes)

    @property
    def instrument_ids(self) -> tuple[int, ...]:
        """The cue's instruments."""
        return tuple(pk for pk, _ in self.instruments)

    @property
    def category(self) -> str:
        """unapproved, approved, inactive, song or license."""
        return models.STATUS_KIND_INFO[self.kind][0]

    @property
    def counted(self) -> bool:
        """In the totals (On hold and Omit aren't)."""
        return models.STATUS_KIND_INFO[self.kind][2]

    @property
    def composition(self) -> bool:
        """Music to write: not a song, licence or inactive status."""
        return self.category in ("unapproved", "approved")


def _percent(part: int, whole: int) -> int:
    """Part of whole as a whole percent, rounded half up; 0 of nothing."""
    return (200 * part + whole) // (2 * whole) if whole > 0 else 0


class _Builder:
    """Makes rows over one set of cues."""

    def __init__(self, rate: timecode.Rate) -> None:
        self.rate = rate

    def row(  # pylint: disable=too-many-arguments
        self,
        label: str,
        cues: list[_Cue],
        base: int,
        *,
        group: str,
        value: Callable[[_Cue], Iterable[object]],
        color: str = "",
        css: str = "",
        length: Callable[[_Cue], int] | None = None,
    ) -> Row:
        """A row for `cues`, as a share of `base` frames.

        `length` gives each cue's part of the row, for themes and
        instruments that play for less than the whole cue.
        """
        frames = sum((length or _whole)(cue) for cue in cues)
        values = sorted(
            {"" if v is None else str(v) for cue in cues for v in value(cue)}
        )
        return Row(
            label=label,
            cues=len(cues),
            duration=timecode.format_duration(frames, self.rate),
            percent=_percent(frames, base),
            color=color,
            css=css,
            group=group,
            values=tuple(values),
        )


def _whole(cue: _Cue) -> int:
    return cue.frames


def _status(cue: _Cue) -> tuple[int | None]:
    return (cue.status_id,)


def _cue_type(cue: _Cue) -> tuple[str]:
    return (cue.cue_type,)


def _always(value: int) -> Callable[[_Cue], tuple[int]]:
    """The filter value of an option row: the option's own id."""

    def get(cue: _Cue) -> tuple[int]:
        del cue
        return (value,)

    return get


def _load(project: models.Project) -> list[_Cue]:
    """The project's active cues, with their themes and instruments."""
    rows = list(
        project.cues.filter(active=True).values_list(
            "id",
            "tc_in",
            "tc_out",
            "status_id",
            "status__kind",
            "cue_type",
            "current_version_id",
            "current_version__sub_status_id",
        )
    )
    themes: dict[int, list[tuple[int, int | None]]] = defaultdict(list)
    for version_id, theme_id, seconds in models.VersionTheme.objects.filter(
        version_id__in=[row[6] for row in rows if row[6] is not None]
    ).values_list("version_id", "theme_id", "seconds"):
        themes[version_id].append((theme_id, seconds))
    instruments: dict[int, list[tuple[int, int | None]]] = defaultdict(list)
    for cue_id, instrument_id, seconds in models.CueInstrument.objects.filter(
        cue__project=project
    ).values_list("cue_id", "instrument_id", "seconds"):
        instruments[cue_id].append((instrument_id, seconds))
    return [
        _Cue(
            frames=max(tc_out - tc_in, 0)
            if tc_in is not None and tc_out is not None
            else 0,
            status_id=status_id,
            kind=kind or models.StatusKind.TO_DO,
            cue_type=cue_type,
            sub_status_id=sub_status_id,
            themes=tuple(themes.get(version_id or 0, ())),
            instruments=tuple(instruments.get(pk, ())),
        )
        for (
            pk,
            tc_in,
            tc_out,
            status_id,
            kind,
            cue_type,
            version_id,
            sub_status_id,
        ) in rows
    ]


def _totals(build: _Builder, counted: list[_Cue]) -> list[Row]:
    base = sum(cue.frames for cue in counted)
    parts = [
        ("Compositions", "done", [c for c in counted if c.composition]),
        (
            "Original Songs",
            "song",
            [c for c in counted if c.category == "song"],
        ),
        (
            "Licensed",
            "license",
            [c for c in counted if c.category == "license"],
        ),
        ("Total", "total", counted),
    ]
    return [
        build.row(label, cues, base, group="status", value=_status, css=css)
        for label, css, cues in parts
    ]


def _progress(build: _Builder, compositions: list[_Cue]) -> list[Row]:
    base = sum(cue.frames for cue in compositions)
    composed = progress.COMPOSED_KINDS
    revise = summaries.REVISE_KINDS
    parts = [
        ("Composed", "done", [c for c in compositions if c.kind in composed]),
        (
            "Remaining",
            "to-do",
            [c for c in compositions if c.kind not in composed],
        ),
        ("To Revise", "changes", [c for c in compositions if c.kind in revise]),
        (
            "Approved",
            "approved",
            [c for c in compositions if c.category == "approved"],
        ),
        (
            "Unapproved",
            "in-progress",
            [c for c in compositions if c.category == "unapproved"],
        ),
    ]
    return [
        build.row(label, cues, base, group="status", value=_status, css=css)
        for label, css, cues in parts
    ]


def _by_option(  # pylint: disable=too-many-arguments
    build: _Builder,
    cues: list[_Cue],
    options: Iterable[tuple[int, str, str]],
    *,
    group: str,
    ids: Callable[[_Cue], Iterable[int | None]],
    spans: Callable[[_Cue], Iterable[tuple[int, int | None]]] | None = None,
) -> list[Row]:
    """A row per option (id, label, colour) that has cues, in order.

    With `spans` (option id, seconds), an option that plays for a set
    number of seconds counts those, not the whole cue.
    """
    base = sum(cue.frames for cue in cues)
    rows = []
    for pk, label, color in options:
        members = [cue for cue in cues if pk in tuple(ids(cue))]
        if members:
            rows.append(
                build.row(
                    label,
                    members,
                    base,
                    group=group,
                    value=_always(pk),
                    color=color,
                    length=None
                    if spans is None
                    else _span_length(pk, spans, build.rate),
                )
            )
    return rows


def _span_length(
    pk: int,
    spans: Callable[[_Cue], Iterable[tuple[int, int | None]]],
    rate: timecode.Rate,
) -> Callable[[_Cue], int]:
    """A cue's frames for option `pk`: its seconds if set, else the cue."""

    def length(cue: _Cue) -> int:
        for option, seconds in spans(cue):
            if option == pk and seconds is not None:
                return round(seconds * rate.fps)
        return cue.frames

    return length


def _deadlines(
    project: models.Project, today: datetime.date
) -> tuple[list[DeadlineRow], models.Deadline | None]:
    chosen = summaries.pace_deadline(project, today)
    rows = [
        DeadlineRow(
            pk=deadline.pk,
            label=str(deadline),
            date=deadline.end_date,
            days_left=(deadline.end_date - today).days,
            chosen=chosen is not None and deadline.pk == chosen.pk,
        )
        for deadline in project.deadlines.order_by("end_date", "pk")
    ]
    return rows, chosen


def for_project(
    project: models.Project, today: datetime.date | None = None
) -> Stats:
    """Collects the panel's figures (a handful of queries)."""
    today = today or timezone.localdate()
    build = _Builder(project.rate)
    active = _load(project)
    counted = [cue for cue in active if cue.counted]
    compositions = [cue for cue in counted if cue.composition]
    deadlines, chosen = _deadlines(project, today)
    statuses = [(s.pk, s.label, s.color) for s in project.statuses.all()]
    kinds = [
        (value, label)
        for value, label in models.Cue.CueType.choices
        if any(cue.cue_type == value for cue in counted)
    ]
    base = sum(cue.frames for cue in counted)
    return Stats(
        totals=_totals(build, counted),
        progress=_progress(build, compositions),
        pace=summaries.pace(project, today),
        pace_label=str(chosen) if chosen is not None else "",
        deadlines=deadlines,
        status=_by_option(
            build,
            active,
            statuses,
            group="status",
            ids=lambda cue: (cue.status_id,),
        ),
        cue_type=[
            build.row(
                label,
                [cue for cue in counted if cue.cue_type == value],
                base,
                group="cue_type",
                value=_cue_type,
            )
            for value, label in kinds
        ],
        sub_status=_by_option(
            build,
            counted,
            [(o.pk, o.label, o.color) for o in project.sub_statuses.all()],
            group="sub_status",
            ids=lambda cue: (cue.sub_status_id,),
        ),
        themes=_by_option(
            build,
            counted,
            [(o.pk, o.name, o.color) for o in project.themes.all()],
            group="themes",
            ids=lambda cue: cue.theme_ids,
            spans=lambda cue: cue.themes,
        ),
        instrumentation=_by_option(
            build,
            counted,
            [(o.pk, o.name, o.color) for o in project.instruments.all()],
            group="instrumentation",
            ids=lambda cue: cue.instrument_ids,
            spans=lambda cue: cue.instruments,
        ),
        details=[
            (
                "Runtime",
                timecode.format_duration(
                    progress.runtime(project), project.rate
                ),
            ),
            ("Frame Rate", project.rate.label),
            (
                "Reels",
                str(
                    project.pictures.exclude(
                        name=project_service.PLACEHOLDER_PICTURE
                    ).count()
                ),
            ),
        ],
    )
