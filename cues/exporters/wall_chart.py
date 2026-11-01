"""The wall chart PDF.

A card per cue (number, title, duration, status color, version, the
turnover it was written to), grouped by reel, to print and pin up.
"""

from collections.abc import Iterable
import dataclasses
import datetime

from cues import models
from cues import timecode
from cues.api import grid
from cues.exporters import pdf


@dataclasses.dataclass(frozen=True)
class Card:
    """One cue on the wall chart."""

    number: str
    title: str
    duration: str
    status: str
    color: str
    version: str
    written_to: str


def ordered_cues(
    project: models.Project, cue_ids: Iterable[int] | None
) -> list[models.Cue]:
    """The project's cues in grid order, or just these, in their order."""
    cues = list(
        grid.cues_queryset(project)
        # The current version's turnover too: prefetching every version
        # doesn't fill the joined current_version.
        .select_related("current_version__written_to__turnover")
        .prefetch_related("versions__written_to__turnover")
    )
    if cue_ids is None:
        return cues
    by_id = {cue.pk: cue for cue in cues}
    return [by_id[pk] for pk in cue_ids if pk in by_id]


def turnover(version: models.CueVersion | None) -> str:
    """The turnover a version was written to, e.g. 'c0917'."""
    if version is None or version.written_to is None:
        return ""
    return str(version.written_to.turnover)


def reel(cue: models.Cue) -> str:
    """e.g. 'Reel 1'."""
    return f"Reel {cue.picture.name}"


def wall_chart(
    project: models.Project,
    cue_ids: Iterable[int] | None = None,
    today: datetime.date | None = None,
) -> bytes:
    """The wall chart PDF."""
    reels: dict[str, list[Card]] = {}
    for cue in ordered_cues(project, cue_ids):
        version = cue.current_version
        duration = cue.duration
        reels.setdefault(reel(cue), []).append(
            Card(
                number=cue.number,
                title=cue.title,
                duration=""
                if duration is None
                else timecode.format_duration(max(duration, 0), project.rate),
                status=cue.status.label if cue.status else "",
                color=cue.status.color if cue.status else "#9ca3af",
                version=version.label if version else "",
                written_to=turnover(version),
            )
        )
    return pdf.render(
        "cues/pdf/wall_chart.html",
        {"project": project, "reels": reels, "today": today},
    )
