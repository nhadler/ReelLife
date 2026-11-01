"""The cue map: a project's runtime with every cue at its place.

Reels are laid end to end by their current picture version, and each
active cue is a block at its position, coloured by status. Drawn as SVG
(the CSP forbids inline styles, but SVG geometry and fill are attributes).
"""

from collections.abc import Callable, Iterable
import dataclasses

from cues import models
from cues.services import progress

WIDTH = 1000  # SVG user units across the whole runtime
MIN_BLOCK = 2.0  # so a 5-second cue in a two-hour film is still visible
UNSET_COLOR = "#6b7280"


@dataclasses.dataclass(frozen=True)
class Segment:
    """A picture's place on the map: its current IN/OUT."""

    picture_id: int
    name: str
    tc_in: int
    tc_out: int


@dataclasses.dataclass(frozen=True)
class CueSpot:
    """What the map needs to know about a cue."""

    number: str
    title: str
    status: str
    color: str
    picture_id: int
    tc_in: int
    tc_out: int | None
    todo: bool = False  # still to write: drawn quietly


@dataclasses.dataclass(frozen=True)
class Rect:
    """An SVG rectangle in map units, with a tooltip."""

    x: float
    width: float
    label: str
    color: str = UNSET_COLOR
    todo: bool = False


@dataclasses.dataclass(frozen=True)
class CueMap:
    """Reels and cue blocks; empty when no picture has IN/OUT yet."""

    reels: list[Rect]
    cues: list[Rect]

    @property
    def empty(self) -> bool:
        """No timeline to draw."""
        return not self.reels


def layout(
    segments: Iterable[Segment],
    cues: Iterable[CueSpot],
    title: Callable[[str], str] = lambda name: f"Reel {name}",
) -> CueMap:
    """Places reels end to end and cues within them.

    Cues in a picture without IN/OUT, or with no IN, aren't drawn. A cue
    is clamped to its reel; a cue without OUT gets the minimum width.
    `title` names a reel from its name (Project.picture_title).
    """
    usable = [s for s in segments if s.tc_out > s.tc_in]
    total = sum(s.tc_out - s.tc_in for s in usable)
    if total <= 0:
        return CueMap([], [])
    scale = WIDTH / total
    reels = []
    starts: dict[int, tuple[float, Segment]] = {}
    offset = 0.0
    for segment in usable:
        width = (segment.tc_out - segment.tc_in) * scale
        reels.append(
            Rect(round(offset, 2), round(width, 2), title(segment.name))
        )
        starts[segment.picture_id] = (offset, segment)
        offset += width
    blocks = []
    for cue in cues:
        if cue.picture_id not in starts:
            continue
        reel_x, segment = starts[cue.picture_id]
        length = segment.tc_out - segment.tc_in
        start = min(max(cue.tc_in - segment.tc_in, 0), length)
        end = (
            start
            if cue.tc_out is None
            else min(max(cue.tc_out - segment.tc_in, start), length)
        )
        width = max((end - start) * scale, MIN_BLOCK)
        label = " ".join(p for p in (cue.number, cue.title) if p)
        if cue.status:
            label += f" ({cue.status})"
        blocks.append(
            Rect(
                round(reel_x + start * scale, 2),
                round(width, 2),
                label,
                cue.color or UNSET_COLOR,
                cue.todo,
            )
        )
    return CueMap(reels, blocks)


def for_project(project: models.Project) -> CueMap:
    """The map for a project, from current picture versions."""
    current = progress.current_picture_versions(project)
    versions = models.PictureVersion.objects.in_bulk(list(current.values()))
    segments = []
    for picture in project.pictures.order_by("order", "name"):
        version = versions.get(current.get(picture.pk, -1))
        if version and version.tc_in is not None and version.tc_out is not None:
            segments.append(
                Segment(picture.pk, picture.name, version.tc_in, version.tc_out)
            )
    cues = [
        CueSpot(
            number=cue.number,
            title=cue.title,
            status=cue.status.label if cue.status else "",
            color=cue.status.color if cue.status else UNSET_COLOR,
            picture_id=cue.picture_id,
            tc_in=cue.tc_in,
            tc_out=cue.tc_out,
            todo=cue.status is None
            or cue.status.kind == models.StatusKind.TO_DO,
        )
        for cue in project.cues.filter(active=True, tc_in__isnull=False)
        .select_related("status")
        .order_by("tc_in")
        if cue.tc_in is not None
    ]
    return layout(segments, cues, project.picture_title)
