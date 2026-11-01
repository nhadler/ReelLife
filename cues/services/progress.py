"""Progress and conform maths.

Pure functions over plain values, so the header, stats panel, exports and
tests all agree; `for_project` gathers those values from the database.
"""

from collections.abc import Iterable
import dataclasses

from cues import models
from cues import timecode

# Changes, Done, Approved and Completed: the music was written at least
# once.
COMPOSED_KINDS = frozenset(
    kind for kind, info in models.STATUS_KIND_INFO.items() if info[1]
)
_SONG_KINDS = frozenset({models.StatusKind.SONG, models.StatusKind.LICENSE})


@dataclasses.dataclass(frozen=True)
class CueFacts:
    """What progress needs to know about one cue."""

    frames: int  # duration; 0 when IN or OUT is unset
    kind: str  # status kind; to_do when the cue has no status
    active: bool


@dataclasses.dataclass(frozen=True)
class Totals:
    """A count of cues and their summed duration."""

    cues: int = 0
    frames: int = 0

    def plus(self, frames: int) -> "Totals":
        """Returns these totals with one more cue."""
        return Totals(self.cues + 1, self.frames + frames)


@dataclasses.dataclass(frozen=True)
class Segment:
    """One part of the header's progress bar."""

    label: str
    css: str  # progress-bar__<css>
    frames: int
    percent: int  # of the total, rounded
    start: float  # where it begins, 0-100
    width: float  # its exact share, 0-100


# The progress bar's parts, left to right: composed first.
BAR_GROUPS: tuple[tuple[str, str, frozenset[str]], ...] = (
    (
        "Approved",
        "approved",
        frozenset({models.StatusKind.APPROVED, models.StatusKind.COMPLETED}),
    ),
    ("Composed", "done", frozenset({models.StatusKind.DONE})),
    ("Revisions", "changes", frozenset({models.StatusKind.CHANGES})),
    ("In Progress", "in-progress", frozenset({models.StatusKind.IN_PROGRESS})),
    ("Rewrite", "redo", frozenset({models.StatusKind.REDO})),
    ("To Write", "to-do", frozenset({models.StatusKind.TO_DO})),
    ("Original Songs", "song", frozenset({models.StatusKind.SONG})),
    ("Licensed", "license", frozenset({models.StatusKind.LICENSE})),
)


@dataclasses.dataclass(frozen=True)
class Progress:
    """Composed vs. total music, duration-weighted."""

    composed: Totals
    total: Totals
    # Counted frames per status kind, for the progress bar.
    by_kind: dict[str, int] = dataclasses.field(default_factory=dict)

    def segments(self) -> list[Segment]:
        """The progress bar's non-empty parts, each a share of the total."""
        if self.total.frames <= 0:
            return []
        segments = []
        start = 0
        for label, css, kinds in BAR_GROUPS:
            frames = sum(self.by_kind.get(kind, 0) for kind in kinds)
            if frames:
                percent = (200 * frames + self.total.frames) // (
                    2 * self.total.frames
                )
                segments.append(
                    Segment(
                        label,
                        css,
                        frames,
                        percent,
                        start=100 * start / self.total.frames,
                        width=100 * frames / self.total.frames,
                    )
                )
                start += frames
        return segments

    @property
    def remaining(self) -> Totals:
        """What's left to compose."""
        return Totals(
            self.total.cues - self.composed.cues,
            self.total.frames - self.composed.frames,
        )

    @property
    def percent(self) -> int:
        """Composed share of total duration, rounded half up.

        By duration, not cue count: 85 s of 217 s is 39%.
        """
        if self.total.frames <= 0:
            return 0
        return (200 * self.composed.frames + self.total.frames) // (
            2 * self.total.frames
        )

    def details(self, rate: timecode.Rate) -> tuple[str, str, str]:
        """The header's figures without their labels.

        e.g. ('1 / 2 cues (39%)', '1:25 of 3:37', '1 cue (2:12)').
        """
        fmt = timecode.format_duration
        return (
            f"{self.composed.cues} / {self.total.cues} "
            f"{_cues(self.total.cues)} ({self.percent}%)",
            f"{fmt(self.composed.frames, rate)} of "
            f"{fmt(self.total.frames, rate)}",
            f"{self.remaining.cues} {_cues(self.remaining.cues)} "
            f"({fmt(self.remaining.frames, rate)})",
        )

    def summary(self, rate: timecode.Rate) -> tuple[str, str]:
        """Returns the header's two lines.

        e.g. ('Composed 1 / 2 cues (39%)  1:25 of 3:37',
        'Remaining 1 cue (2:12)').
        """
        cues, durations, remaining = self.details(rate)
        return f"Composed {cues}  {durations}", f"Remaining {remaining}"


def _cues(count: int) -> str:
    return "cue" if count == 1 else "cues"


def counts(
    kind: str, active: bool, *, include_songs_and_licensed: bool
) -> bool:
    """Whether a cue counts toward progress and the project's totals.

    Inactive cues and statuses of kind on_hold or omit never count; songs
    and licensed music only with the music totals setting on.
    """
    info = models.STATUS_KIND_INFO.get(kind)
    if not active or info is None or not info[2]:
        return False
    return include_songs_and_licensed or kind not in _SONG_KINDS


def includes_songs_and_licensed() -> bool:
    """App Settings' music totals: compositions, or songs and licensed too."""
    return (
        models.AppSettings.load().music_totals
        == models.AppSettings.MusicTotals.ALL
    )


def progress(
    cues: Iterable[CueFacts], *, include_songs_and_licensed: bool
) -> Progress:
    """Totals the cues that count toward progress.

    Args:
        cues: Every cue in the project.
        include_songs_and_licensed: The music totals setting; when False
            only compositions count.

    Returns:
        Composed and total cues and durations. Inactive cues and statuses
        of kind on_hold or omit never count.
    """
    composed = total = Totals()
    by_kind: dict[str, int] = {}
    for cue in cues:
        if not counts(
            cue.kind,
            cue.active,
            include_songs_and_licensed=include_songs_and_licensed,
        ):
            continue
        frames = max(cue.frames, 0)
        total = total.plus(frames)
        by_kind[cue.kind] = by_kind.get(cue.kind, 0) + frames
        if cue.kind in COMPOSED_KINDS:
            composed = composed.plus(frames)
    return Progress(composed, total, by_kind)


def for_project(project: models.Project) -> Progress:
    """Progress for a project, using the music totals setting."""
    rows = project.cues.values_list("tc_in", "tc_out", "status__kind", "active")
    facts = (
        CueFacts(
            frames=(tc_out - tc_in)
            if tc_in is not None and tc_out is not None
            else 0,
            kind=kind or models.StatusKind.TO_DO,
            active=active,
        )
        for tc_in, tc_out, kind, active in rows
    )
    return progress(
        facts, include_songs_and_licensed=includes_songs_and_licensed()
    )


@dataclasses.dataclass(frozen=True)
class Conform:
    """How a cue's current version relates to the current picture.

    Attributes:
        needs_conform: The version was written to an older picture
            version than the picture's current one.
        start_offset: Cue IN minus version IN, in frames (None when either
            is unset).
        duration_difference: Cue duration minus version duration.
    """

    needs_conform: bool
    start_offset: int | None
    duration_difference: int | None


def conform(
    *,
    written_to_id: int | None,
    current_picture_version_id: int | None,
    cue_in: int | None,
    cue_out: int | None,
    version_in: int | None,
    version_out: int | None,
) -> Conform:
    """Compares a cue's current position with its current version.

    A cue with no version, or a version not tied to a picture version,
    doesn't need conform: there is nothing to compare against.
    """
    needs = (
        written_to_id is not None
        and current_picture_version_id is not None
        and written_to_id != current_picture_version_id
    )
    offset = None
    if cue_in is not None and version_in is not None:
        offset = cue_in - version_in
    difference = None
    if (
        cue_in is not None
        and cue_out is not None
        and version_in is not None
        and version_out is not None
    ):
        difference = (cue_out - cue_in) - (version_out - version_in)
    return Conform(needs, offset, difference)


def current_picture_versions(project: models.Project) -> dict[int, int]:
    """Maps each picture id to its current picture version id.

    The current version is the one from the latest turnover that includes
    the picture (by received date, then id). One query.
    """
    current: dict[int, int] = {}
    rows = (
        models.PictureVersion.objects.filter(picture__project=project)
        .order_by("turnover__received_date", "turnover_id")
        .values_list("picture_id", "id")
    )
    for picture_id, version_id in rows:
        current[picture_id] = version_id  # later turnovers overwrite
    return current


def sequence_numbers(project: models.Project) -> dict[int, int]:
    """Maps active cue ids to their cue sheet sequence number (#).

    Order: picture order, then IN, then cue number.
    """
    ordered = (
        project.cues.filter(active=True)
        .order_by("picture__order", "picture__name", "tc_in", "number")
        .values_list("id", flat=True)
    )
    return {cue_id: n for n, cue_id in enumerate(ordered, start=1)}


def runtime(project: models.Project) -> int:
    """Sum of each picture's current length (OUT minus IN), in frames."""
    current = current_picture_versions(project)
    lengths = models.PictureVersion.objects.filter(
        id__in=current.values()
    ).values_list("tc_in", "tc_out")
    return sum(
        tc_out - tc_in
        for tc_in, tc_out in lengths
        if tc_in is not None and tc_out is not None
    )
